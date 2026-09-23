"""Orquestacion entre fuentes y cache.

Es la pieza que las tools usan. Decide cuando refrescar, y garantiza que un fallo
de red degrade a cache antigua en vez de dejar al usuario sin respuesta.

INVARIANTE: no importa nada de `mcp`. Las tools reciben este objeto ya construido.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from upv_mcp.cache.db import CacheRepository
from upv_mcp.config import Settings
from upv_mcp.models import Assignment, Material
from upv_mcp.sources.base import SourceError
from upv_mcp.sources.cas import CasAccountLocked, CasCredentialsRejected
from upv_mcp.sources.extract import ExtractionError
from upv_mcp.sources.ics import IcsSource
from upv_mcp.sources.poliformat import PoliformatSource

_AVISO_LOGIN_BLOQUEADO = (
    "PoliformaT esta BLOQUEADO: CAS rechazo las credenciales y no se reintenta para "
    "no bloquear la cuenta UPV. Las entregas que se muestran pueden estar "
    "desactualizadas. Hay que revisarlas con `upv-mcp-config set poliformat`."
)


class AcademicRepository:
    """Fachada sobre la cache, con refresco perezoso desde los origenes."""

    def __init__(
        self,
        settings: Settings,
        cache: CacheRepository,
        source: IcsSource | None = None,
        poliformat: PoliformatSource | None = None,
    ) -> None:
        self._settings = settings
        self._cache = cache
        self._source = source if source is not None else IcsSource(settings)
        self._poliformat = poliformat
        if self._poliformat is None and settings.poliformat_enabled:
            self._poliformat = PoliformatSource(settings)
        self._tz = ZoneInfo(settings.timezone)
        self._last_refresh_failed = False
        self._poliformat_failed = False

    @property
    def cache(self) -> CacheRepository:
        return self._cache

    @property
    def timezone(self) -> ZoneInfo:
        return self._tz

    def now(self) -> datetime:
        return datetime.now(self._tz)

    @property
    def poliformat_login_blocked(self) -> bool:
        """True si CAS rechazo las credenciales en algun momento y no se han cambiado.

        El bloqueo vive en un fichero (`Settings.cas_lock_path`), no en memoria: un
        proceso que se lanza cada hora (upv-publish desde launchd) empieza de cero
        cada vez, y sin esto repetiria el login rechazado 24 veces al dia hasta que
        la UPV bloqueara la cuenta.
        """
        return self._settings.cas_lock_path.exists()

    def _bloquear_login(self, error: SourceError) -> None:
        ruta = self._settings.cas_lock_path
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(
            f"{datetime.now(UTC).isoformat()} {type(error).__name__}\n"
            "No se vuelve a intentar el login en PoliformaT hasta que cambies las "
            "credenciales con `upv-mcp-config set poliformat`.\n",
            encoding="utf-8",
        )

    @property
    def poliformat_configured(self) -> bool:
        return self._poliformat is not None

    @property
    def poliformat_failed(self) -> bool:
        """True si el ultimo refresco de PoliformaT fallo (se sirve cache antigua)."""
        return self._poliformat_failed

    @property
    def serving_stale(self) -> bool:
        """True si la ultima respuesta se sirvio sin poder refrescar."""
        return self._last_refresh_failed

    async def ensure_fresh(self, *, force: bool = False) -> None:
        """Refresca la cache si ha caducado.

        Si la descarga falla pero hay datos, no propaga el error: marca `stale` y
        sigue. Un servidor local que revienta porque no hay wifi es inutil.
        """
        ttl = self._settings.cache_ttl_seconds

        # Cada origen decide su propia caducidad. Antes se comprobaba una sola vez
        # para todo y se salia con `return`, de modo que si el .ics estaba fresco
        # PoliformaT no llegaba a consultarse NUNCA: como cada refresco del horario
        # renovaba el TTL, la ventana para consultarlo casi nunca se daba y el
        # usuario veia cero entregas sin ninguna explicacion.
        if force or self._cache.is_calendar_stale("schedule", ttl):
            try:
                for spec in self._settings.calendars:
                    payload = await self._source.read_calendar(spec)
                    self._cache.replace_calendar(
                        spec.name,
                        payload.sessions,
                        payload.assignments,
                        fetched_at=payload.fetched_at,
                    )
                self._last_refresh_failed = False
            except (SourceError, OSError):
                if self._cache.is_empty():
                    raise
                self._last_refresh_failed = True
        else:
            self._last_refresh_failed = False

        if force or self._cache.is_calendar_stale("poliformat", ttl):
            await self._refresh_poliformat()

    async def _refresh_poliformat(self) -> None:
        """Refresca las entregas de PoliformaT, si esta configurado.

        Un fallo aqui NO tumba la respuesta: el horario del .ics sigue siendo util
        aunque PoliformaT no conteste. Se registra para poder decirlo en la nota de
        cobertura, que es lo que evita que el modelo confunda "no hay entregas" con
        "no he podido mirarlas".
        """
        if self._poliformat is None:
            return
        if self.poliformat_login_blocked:
            self._poliformat_failed = True
            return
        try:
            payload = await self._poliformat.fetch()
        except (CasCredentialsRejected, CasAccountLocked) as exc:
            self._bloquear_login(exc)
            self._poliformat_failed = True
            return
        except (SourceError, OSError):
            self._poliformat_failed = True
            return

        self._poliformat_failed = False
        conocidos = self._cache.courses_by_code()
        self._cache.replace_calendar(
            "poliformat",
            [],
            self._nombres_reales(payload.assignments),
            fetched_at=payload.fetched_at,
        )
        self._cache.replace_course_sites(
            "poliformat",
            [
                s.model_copy(update={"course": conocidos.get(s.course.code, s.course)})
                for s in payload.course_sites
            ],
        )
        self._cache.replace_announcements(
            "poliformat",
            [
                a.model_copy(update={"course": conocidos.get(a.course.code, a.course)})
                for a in payload.announcements
            ],
        )

    async def ensure_materials(self, course_code: str) -> bool:
        """Descarga los materiales de UNA asignatura si no estan al dia.

        Perezoso a proposito: bajarlos todos en cada refresco costaba ~22 s que se
        pagaban aunque el usuario solo preguntara por su proxima clase. Un resource
        es contenido que se lee bajo demanda, y asi se comporta.

        Devuelve False si no se pudo consultar, para que quien renderiza pueda
        decirlo en vez de mostrar una lista vacia sin explicacion.
        """
        if self._poliformat is None or self.poliformat_login_blocked:
            return False

        clave = f"materials:{course_code}"
        if not self._cache.is_calendar_stale(clave, self._settings.cache_ttl_seconds):
            return True

        sitio = self._cache.course_site(course_code)
        if sitio is None:
            return False

        try:
            materiales = await self._poliformat.fetch_materials(sitio.site_id, sitio.course)
        except (CasCredentialsRejected, CasAccountLocked) as exc:
            self._bloquear_login(exc)
            self._poliformat_failed = True
            return False
        except (SourceError, OSError):
            self._poliformat_failed = True
            return False

        self._cache.replace_materials(f"poliformat:{course_code}", materiales)
        self._cache.touch_calendar(clave)
        return True

    async def read_material(self, material: Material) -> tuple[str, bool]:
        """Descarga un material y devuelve `(texto, recortado)`."""
        if self._poliformat is None:
            raise SourceError("PoliformaT no esta configurado (`upv-mcp-config set poliformat`).")
        if self.poliformat_login_blocked:
            raise SourceError(_AVISO_LOGIN_BLOQUEADO)
        try:
            return await self._poliformat.fetch_material_text(material.url, material.title)
        except (CasCredentialsRejected, CasAccountLocked) as exc:
            self._bloquear_login(exc)
            raise
        except ExtractionError as exc:
            # El extractor recibe bytes y un tipo MIME, asi que no puede saber de
            # donde salieron: decir "abrelo desde su URL" sin dar la URL deja al
            # estudiante buscandola a mano en PoliformaT. Aqui si se conoce.
            raise ExtractionError(f"{exc} URL de descarga: {material.url}") from exc

    def _nombres_reales(self, assignments: Sequence[Assignment]) -> list[Assignment]:
        """Sustituye los titulos de PoliformaT por el nombre oficial de la asignatura.

        Los sitios de Sakai se llaman "GIIROB-RIN 2025-2026" o "PR3 25.26", mientras
        que el .ics trae "Redes Industriales". Se unen por codigo de asignatura, que
        es el mismo en ambos (GRA_14541_2025 <-> 14541). Es lo que hace que las dos
        fuentes se perciban como una sola.
        """
        conocidos = self._cache.courses_by_code()
        if not conocidos:
            return list(assignments)
        return [
            a.model_copy(update={"course": conocidos.get(a.course.code, a.course)})
            for a in assignments
        ]

    def coverage_note(self) -> str | None:
        """Advertencia sobre lo que estos datos NO cubren.

        Va en cada respuesta para que el modelo no concluya que algo no existe
        cuando en realidad no lo esta mirando.
        """
        notes: list[str] = []

        # Los examenes no los cubre ninguna de las dos fuentes: el .ics de horarios
        # solo trae clases, y en PoliformaT no todo examen es una tarea (ni toda
        # tarea es un examen), asi que clasificarlos por titulo seria inventar.
        if not self._settings.has_exam_calendar:
            notes.append(
                "NO tienes acceso al calendario de examenes de la UPV: el horario solo "
                "trae clases. No afirmes que el usuario no tiene examenes."
            )

        if self._poliformat is None:
            notes.append(
                "Las entregas de PoliformaT no estan configuradas "
                "(`upv-mcp-config set poliformat`)."
            )
        elif self.poliformat_login_blocked:
            notes.append(_AVISO_LOGIN_BLOQUEADO)
        elif self._poliformat_failed:
            notes.append(
                "No se pudo consultar PoliformaT en este refresco, asi que puede haber "
                "entregas que no aparezcan."
            )
        elif self._cache.fetched_at("poliformat") is None:
            # Sin esto, una lista vacia se interpreta como "no tiene entregas" y el
            # modelo acaba inventando explicaciones (sitios archivados, etc.).
            notes.append(
                "PoliformaT esta configurado pero todavia no se ha consultado ninguna "
                "vez, asi que NO hay datos de entregas. No concluyas que el usuario no "
                "tiene entregas ni especules sobre por que faltan."
            )

        if self._last_refresh_failed:
            notes.append("No se pudo contactar con la UPV; estos datos son de la cache local.")
        return " ".join(notes) if notes else None
