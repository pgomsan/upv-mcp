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
from datetime import datetime
from typing import Any, Final
from zoneinfo import ZoneInfo

from upv_mcp.config import Settings
from upv_mcp.models import Assignment, Course, EventKind, SourceName
from upv_mcp.sources.base import SourcePayload
from upv_mcp.sources.sakai import SakaiClient

#: Los sitios de asignatura son GRA_<codigo>_<curso>. CEN_*/CDL_* no lo son.
_SITE_ID: Final = re.compile(r"^(?P<prefijo>[A-Z]+)_(?P<codigo>\d{4,6})_(?P<curso>\d{4})$")

_TAGS: Final = re.compile(r"<[^>]+>")


def parse_site_id(site_id: str) -> tuple[str, int] | None:
    """`GRA_14541_2025` -> `("14541", 2025)`. None si no es una asignatura."""
    match = _SITE_ID.match(site_id.strip())
    if match is None:
        return None
    return match.group("codigo"), int(match.group("curso"))


def _limpiar(texto: str | None) -> str | None:
    """Sakai devuelve HTML con entidades en titulos y descripciones."""
    if not texto:
        return None
    limpio = html.unescape(_TAGS.sub(" ", texto))
    limpio = " ".join(limpio.split())
    return limpio or None


def _parse_instante(valor: str | None, tz: ZoneInfo) -> datetime | None:
    """`2026-04-15T11:00:00Z` -> datetime aware en hora de Valencia."""
    if not valor:
        return None
    try:
        return datetime.fromisoformat(valor.replace("Z", "+00:00")).astimezone(tz)
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

    # -- Contrato AcademicSource --------------------------------------------------

    async def fetch(self, *, force_refresh: bool = False) -> SourcePayload:
        """Descarga entregas y fechas limite de las asignaturas activas."""
        async with SakaiClient(self._settings) as cliente:
            tareas = (await cliente.get_json("/direct/assignment/my.json")).get(
                "assignment_collection", []
            )
            eventos = (await cliente.get_json("/direct/calendar/my.json")).get(
                "calendar_collection", []
            )
            sitios = (await cliente.get_json("/direct/site.json")).get("site_collection", [])

        # Las asignaturas se descubren desde las tres fuentes: site.json viene
        # incompleto y por si solo se dejaria entregas fuera.
        ids: set[str] = {str(s.get("id", "")) for s in sitios}
        ids |= {str(t.get("context", "")) for t in tareas}
        ids |= {str(e.get("siteId", "")) for e in eventos}
        activos = self._cursos_activos({i for i in ids if i})

        titulos = {str(s.get("id", "")): _limpiar(s.get("title")) for s in sitios}
        for sid, curso in activos.items():
            if titulo := titulos.get(sid):
                activos[sid] = Course(code=curso.code, name=titulo)

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

        return SourcePayload(assignments=unicas, fetched_at=datetime.now(self._tz))
