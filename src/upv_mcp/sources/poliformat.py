"""Origen PoliformaT (Sakai) via la API REST `/direct/`.

Formato observado contra la API real, verificado sobre 82 entregas y 106 eventos:

    id de sitio:  GRA_14541_2025   -> asignatura 14541, curso 2025-26
                  CEN_R_2025, CDL_0_2023  -> no son asignaturas
    entrega:      dueTimeString = "2026-04-15T11:00:00Z"  (UTC, ISO con Z)
                  context = id del sitio, status = OPEN|CLOSED|DRAFT
    calendario:   type = "Deadline" | "Activity"; 48 de 106 llevan assignmentId,
                  que son la MISMA entrega vista dos veces

Decisiones que costaria redescubrir:

* NO se clasifica ninguna tarea como examen. Hay tareas tituladas "Examen parcial"
  y examenes que no son tarea ninguna: cualquier heuristica por titulo produce
  falsos positivos y negativos. Los examenes tienen su propio calendario en la UPV,
  todavia por localizar, y hasta entonces se declara que no se cubren.
* Los eventos de calendario con `assignmentId` se descartan: ya vienen como tarea.
* Los de tipo "Activity" ("Inicio de las clases") no son fechas limite y se ignoran.
* `/direct/site.json` viene incompleto (devolvio 8 sitios GRA mientras las entregas
  referenciaban 9), asi que las asignaturas se descubren tambien desde las propias
  entregas.
"""

from __future__ import annotations

import html
import re
from datetime import UTC, datetime
from typing import Any, Final
from zoneinfo import ZoneInfo

from upv_mcp.config import Settings
from upv_mcp.models import (
    Announcement,
    Assignment,
    Course,
    CourseSite,
    EventKind,
    Material,
    SourceName,
    Submission,
    SubmissionStatus,
)
from upv_mcp.sources.base import SourceError, SourcePayload
from upv_mcp.sources.extract import extract
from upv_mcp.sources.sakai import SakaiClient

#: Tope de descarga. Por encima de esto, mejor abrir la URL en el navegador: los
#: ficheros grandes de PoliformaT suelen ser zips y videos, que no dan texto.
MAX_DOWNLOAD_BYTES: Final = 25 * 1024 * 1024

#: Los sitios de asignatura son GRA_<codigo>_<curso>. CEN_*/CDL_* no lo son.
_SITE_ID: Final = re.compile(r"^(?P<prefijo>[A-Z]+)_(?P<codigo>\d{4,6})_(?P<curso>\d{4})$")

_TAGS: Final = re.compile(r"<[^>]+>")
_ESPACIO_ANTES_PUNTUACION: Final = re.compile(r"\s+([.,;:!?)\]])")


def parse_site_id(site_id: str) -> tuple[str, int] | None:
    """`GRA_14541_2025` -> `("14541", 2025)`. None si no es una asignatura."""
    match = _SITE_ID.match(site_id.strip())
    if match is None:
        return None
    return match.group("codigo"), int(match.group("curso"))


def _limpiar(texto: str | None) -> str | None:
    """Sakai devuelve HTML con entidades en titulos y descripciones.

    Las etiquetas se sustituyen por un espacio para no pegar palabras
    ("fin<br>Otra"), y despues se quita el espacio sobrante antes de un signo de
    puntuacion: si no, "revisa el <b>apartado 3</b>." acaba como "apartado 3 .".
    """
    if not texto:
        return None
    limpio = html.unescape(_TAGS.sub(" ", texto))
    limpio = " ".join(limpio.split())
    limpio = _ESPACIO_ANTES_PUNTUACION.sub(r"\1", limpio)
    return limpio or None


def _parse_instante(valor: str | None, tz: ZoneInfo) -> datetime | None:
    """`2026-04-15T11:00:00Z` -> datetime aware en hora de Valencia."""
    if not valor:
        return None
    try:
        return datetime.fromisoformat(valor.replace("Z", "+00:00")).astimezone(tz)
    except ValueError:
        return None


#: Basura de proyectos subidos enteros (PyCharm, git, macOS). Un profesor sube un
#: repositorio y aparecen 200 ficheros de .idea/ que a un alumno no le sirven de nada.
_RUIDO: Final = re.compile(
    r"(^|/)(\.git|\.idea|\.vscode|__pycache__|__MACOSX|\.ipynb_checkpoints)(/|$)",
    re.IGNORECASE,
)


def _recortar(texto: str | None, tope: int) -> str | None:
    """Recorta sin dejar la frase colgando en mitad de una palabra."""
    if not texto or len(texto) <= tope:
        return texto
    return texto[:tope].rsplit(" ", 1)[0] + "..."


def _es_ruido(titulo: str, url: str) -> bool:
    """Descarta ficheros ocultos y metadatos de herramientas de desarrollo."""
    return titulo.startswith(".") or _RUIDO.search(url) is not None


def _fecha_compacta(valor: Any, tz: ZoneInfo) -> datetime | None:  # noqa: ANN401 - JSON de Sakai
    """El tercer formato de fecha de Sakai: `20260204081410971` (YYYYMMDDhhmmssSSS).

    Lo usa `modifiedDate` de los recursos. Llega como cadena y en UTC.
    """
    texto = str(valor or "")
    if len(texto) < 14 or not texto[:14].isdigit():
        return None
    try:
        return datetime.strptime(texto[:14], "%Y%m%d%H%M%S").replace(tzinfo=UTC).astimezone(tz)
    except ValueError:
        return None


def _instante_sakai(campo: Any, tz: ZoneInfo) -> datetime | None:  # noqa: ANN401 - JSON de Sakai
    """Sakai usa DOS formatos de fecha distintos segun la herramienta.

    Tareas:      {"epochSecond": 1776250800, "nano": 0}   -> segundos
    Calendario:  {"time": 1782148140000, "display": "..."} -> MILIsegundos

    Confundirlos coloca los eventos en 1970 o en el ano 58000, asi que se
    distinguen explicitamente en vez de adivinar por magnitud.
    """
    if not isinstance(campo, dict):
        return None
    if isinstance(segundos := campo.get("epochSecond"), int | float):
        return datetime.fromtimestamp(float(segundos), tz=tz)
    if isinstance(milis := campo.get("time"), int | float):
        return datetime.fromtimestamp(float(milis) / 1000.0, tz=tz)
    return None


class PoliformatSource:
    """Entregas de PoliformaT. Cumple el Protocol `AcademicSource`."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._tz = ZoneInfo(settings.timezone)

    @property
    def name(self) -> str:
        return SourceName.POLIFORMAT.value

    # -- Descubrimiento de asignaturas --------------------------------------------

    def _cursos_activos(self, site_ids: set[str]) -> dict[str, Course]:
        """Se queda con los sitios del curso academico mas reciente.

        PoliformaT acumula todos los anos que has estudiado. Arrastrar entregas de
        hace dos cursos solo mete ruido en el contexto del modelo.
        """
        parseados = {sid: parse_site_id(sid) for sid in site_ids}
        validos = {sid: p for sid, p in parseados.items() if p is not None}
        if not validos:
            return {}

        ultimo = max(curso for _, curso in validos.values())
        return {
            sid: Course(code=codigo, name=f"Asignatura {codigo}")
            for sid, (codigo, curso) in validos.items()
            if curso == ultimo
        }

    # -- Mapeo --------------------------------------------------------------------

    def _submission(self, tarea: dict[str, Any]) -> Submission:
        """Extrae el estado de entrega del alumno.

        `/direct/assignment/my.json` devuelve una sola submission por tarea, la del
        usuario autenticado. Cuando no hay ninguna (6 de 82 en datos reales) el
        estado es UNKNOWN, nunca NOT_SUBMITTED: decir "te falta entregar" algo que
        quiza ya esta hecho es peor que reconocer que no se sabe.
        """
        entregas = tarea.get("submissions") or []
        if not entregas or not isinstance(entregas[0], dict):
            return Submission(status=SubmissionStatus.UNKNOWN)

        datos = entregas[0]
        # OJO: el campo `submitted` NO sirve. Vale True en las 76 tareas con
        # submission, incluidas las que Sakai muestra como "No ha empezado".
        # El campo bueno es `userSubmission`, que casa exactamente con la fecha de
        # entrega (38 y 38 en datos reales). Verificado contra la API, no supuesto.
        cuando = datos.get("dateSubmittedEpochSeconds")
        entregado = bool(datos.get("userSubmission")) or bool(cuando)
        corregido = bool(datos.get("graded"))

        # Corregida pero sin registro de entrega a nombre de este alumno (6 casos
        # reales: el proyecto de IHM con un 9,50 y cuatro entregas de PR3). Pasa en
        # trabajo de grupo, donde entrega un miembro, y cuando el profesor califica
        # a mano. Un 9,50 demuestra que el trabajo existe, asi que NOT_SUBMITTED
        # seria falso; pero SUBMITTED afirmaria una entrega suya que no consta. Se
        # queda en UNKNOWN, que es exactamente lo que se sabe.
        if entregado:
            estado = SubmissionStatus.SUBMITTED
        elif corregido:
            estado = SubmissionStatus.UNKNOWN
        else:
            estado = SubmissionStatus.NOT_SUBMITTED

        nota = str(datos.get("grade") or "").strip() or None
        return Submission(
            status=estado,
            submitted_at=(
                datetime.fromtimestamp(float(cuando), tz=self._tz)
                if isinstance(cuando, int | float)
                else None
            ),
            late=datos.get("late") if isinstance(datos.get("late"), bool) else None,
            graded=corregido,
            grade=nota,
            grade_max=str(tarea.get("gradeScaleMaxPoints") or "").strip() or None,
            feedback=_recortar(_limpiar(datos.get("feedbackComment")), 600),
        )

    def _entrega_de_tarea(self, tarea: dict[str, Any], curso: Course) -> Assignment | None:
        vence = _parse_instante(tarea.get("dueTimeString"), self._tz) or _parse_instante(
            tarea.get("closeTimeString"), self._tz
        )
        if vence is None:
            return None

        titulo = _limpiar(tarea.get("title")) or "Entrega sin titulo"
        return Assignment(
            uid=f"poliformat:assignment:{tarea.get('id') or tarea.get('entityId')}",
            # Sin clasificar como examen a proposito: ver el docstring del modulo.
            kind=EventKind.ASSIGNMENT,
            title=titulo,
            course=curso,
            due=vence,
            url=tarea.get("entityURL"),
            source=SourceName.POLIFORMAT,
            submission=self._submission(tarea),
        )

    def _entrega_de_evento(self, evento: dict[str, Any], curso: Course) -> Assignment | None:
        """Convierte un evento de calendario de tipo Deadline en fecha limite."""
        if evento.get("type") != "Deadline" or evento.get("assignmentId"):
            return None  # Activity, o duplicado de una tarea ya recogida.

        vence = _instante_sakai(evento.get("firstTime"), self._tz)
        if vence is None:
            return None

        return Assignment(
            uid=f"poliformat:event:{evento.get('eventId')}",
            kind=EventKind.ASSIGNMENT,
            title=_limpiar(evento.get("title")) or "Fecha limite",
            course=curso,
            due=vence,
            source=SourceName.POLIFORMAT,
        )

    def _material(self, recurso: dict[str, Any], curso: Course) -> Material | None:
        url = recurso.get("url")
        titulo = _limpiar(recurso.get("title"))
        if not url or not titulo:
            return None
        if recurso.get("hidden") or recurso.get("visible") is False:
            return None  # No visible para el alumno: no debe aparecer.
        if _es_ruido(titulo, str(url)):
            return None

        return Material(
            course=curso,
            title=titulo,
            url=str(url),
            content_type=recurso.get("type"),
            updated_at=_fecha_compacta(recurso.get("modifiedDate"), self._tz),
            size_bytes=recurso.get("size") if isinstance(recurso.get("size"), int) else None,
        )

    def _anuncio(self, aviso: dict[str, Any], curso: Course) -> Announcement | None:
        publicado = aviso.get("createdOn")
        if not isinstance(publicado, int | float):
            return None
        cuerpo = _limpiar(aviso.get("body")) or ""
        return Announcement(
            uid=f"poliformat:announcement:{aviso.get('announcementId') or aviso.get('id')}",
            course=curso,
            title=_limpiar(aviso.get("title")) or "Aviso",
            # Recortado: un aviso largo no debe monopolizar el contexto.
            body=cuerpo[:1200] + ("..." if len(cuerpo) > 1200 else ""),
            author=_limpiar(aviso.get("createdByDisplayName")),
            published_at=datetime.fromtimestamp(float(publicado) / 1000.0, tz=self._tz),
            url=aviso.get("entityURL"),
        )

    # -- Contrato AcademicSource --------------------------------------------------

    async def fetch(self, *, force_refresh: bool = False) -> SourcePayload:
        """Descarga entregas, materiales y anuncios de las asignaturas activas."""
        async with SakaiClient(self._settings) as cliente:
            tareas = (await cliente.get_json("/direct/assignment/my.json")).get(
                "assignment_collection", []
            )
            eventos = (await cliente.get_json("/direct/calendar/my.json")).get(
                "calendar_collection", []
            )
            sitios = (await cliente.get_json("/direct/site.json")).get("site_collection", [])
            avisos = (await cliente.get_json("/direct/announcement/user.json")).get(
                "announcement_collection", []
            )

            # Las asignaturas se descubren desde las cuatro fuentes: site.json viene
            # incompleto y por si solo se dejaria entregas fuera.
            ids: set[str] = {str(s.get("id", "")) for s in sitios}
            ids |= {str(t.get("context", "")) for t in tareas}
            ids |= {str(e.get("siteId", "")) for e in eventos}
            ids |= {str(a.get("siteId", "")) for a in avisos}
            activos = self._cursos_activos({i for i in ids if i})

            titulos = {str(s.get("id", "")): _limpiar(s.get("title")) for s in sitios}
            for sid, curso in activos.items():
                if titulo := titulos.get(sid):
                    activos[sid] = Course(code=curso.code, name=titulo)

        # Los materiales NO se descargan aqui: son una peticion por asignatura y se
        # piden bajo demanda al leer su resource (ver fetch_materials).

        entregas: list[Assignment] = []
        for tarea in tareas:
            asignatura = activos.get(str(tarea.get("context", "")))
            if asignatura and (entrega := self._entrega_de_tarea(tarea, asignatura)):
                entregas.append(entrega)
        for evento in eventos:
            asignatura = activos.get(str(evento.get("siteId", "")))
            if asignatura and (entrega := self._entrega_de_evento(evento, asignatura)):
                entregas.append(entrega)

        vistos: set[str] = set()
        unicas: list[Assignment] = []
        for entrega in sorted(entregas, key=lambda a: a.due):
            if entrega.uid not in vistos:
                vistos.add(entrega.uid)
                unicas.append(entrega)

        anuncios: list[Announcement] = []
        for aviso in avisos:
            materia = activos.get(str(aviso.get("siteId", "")))
            if materia and (anuncio := self._anuncio(aviso, materia)):
                anuncios.append(anuncio)
        anuncios.sort(key=lambda a: a.published_at, reverse=True)

        return SourcePayload(
            assignments=unicas,
            announcements=anuncios,
            course_sites=[CourseSite(course=curso, site_id=sid) for sid, curso in activos.items()],
            fetched_at=datetime.now(self._tz),
        )

    async def fetch_material_text(self, url: str, nombre: str) -> tuple[str, bool]:
        """Descarga un fichero y devuelve `(texto, recortado)`.

        Un fichero cada vez y bajo demanda: es lo que permite preguntar por el
        contenido de unos apuntes sin arrastrar los 2065 recursos del usuario.
        """
        async with SakaiClient(self._settings) as cliente:
            datos, tipo = await cliente.download(url, max_bytes=MAX_DOWNLOAD_BYTES)
        return extract(datos, tipo, nombre)

    async def fetch_materials(self, site_id: str, curso: Course) -> list[Material]:
        """Materiales de UNA asignatura, bajo demanda.

        Antes se descargaban los de todas en cada refresco: una peticion por
        asignatura a 0,5 req/s son ~22 s, que se pagaban aunque el usuario solo
        preguntara por su proxima clase. Ahora se piden solo al leer el resource
        correspondiente.

        Se piden metadatos, nunca el contenido de los ficheros.
        """
        async with SakaiClient(self._settings) as cliente:
            try:
                datos = await cliente.get_json(f"/direct/content/site/{site_id}.json")
            except SourceError:
                return []  # Una asignatura sin recursos no es un error.

        materiales = [
            material
            for recurso in datos.get("content_collection", [])
            if (material := self._material(recurso, curso))
        ]
        materiales.sort(key=lambda m: m.title)
        return materiales
