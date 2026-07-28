"""Orquestacion entre fuentes y cache.

Es la pieza que las tools usan. Decide cuando refrescar, y garantiza que un fallo
de red degrade a cache antigua en vez de dejar al usuario sin respuesta.

INVARIANTE: no importa nada de `mcp`. Las tools reciben este objeto ya construido.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from upv_mcp.cache.db import CacheRepository
from upv_mcp.config import Settings
from upv_mcp.sources.base import SourceError
from upv_mcp.sources.ics import IcsSource


class AcademicRepository:
    """Fachada sobre la cache, con refresco perezoso desde los origenes."""

    def __init__(
        self,
        settings: Settings,
        cache: CacheRepository,
        source: IcsSource | None = None,
    ) -> None:
        self._settings = settings
        self._cache = cache
        self._source = source if source is not None else IcsSource(settings)
        self._tz = ZoneInfo(settings.timezone)
        self._last_refresh_failed = False

    @property
    def cache(self) -> CacheRepository:
        return self._cache

    @property
    def timezone(self) -> ZoneInfo:
        return self._tz

    def now(self) -> datetime:
        return datetime.now(self._tz)

    @property
    def serving_stale(self) -> bool:
        """True si la ultima respuesta se sirvio sin poder refrescar."""
        return self._last_refresh_failed

    async def ensure_fresh(self, *, force: bool = False) -> None:
        """Refresca la cache si ha caducado.

        Si la descarga falla pero hay datos, no propaga el error: marca `stale` y
        sigue. Un servidor local que revienta porque no hay wifi es inutil.
        """
        needs_refresh = force or self._cache.is_stale(self._settings.cache_ttl_seconds)
        if not needs_refresh:
            self._last_refresh_failed = False
            return

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

    def coverage_note(self) -> str | None:
        """Advertencia sobre lo que estos datos NO cubren.

        Va en cada respuesta para que el modelo no concluya que algo no existe
        cuando en realidad no lo esta mirando.
        """
        notes: list[str] = []
        if not self._settings.has_exam_calendar:
            notes.append(
                "El calendario configurado es solo de clases: NO incluye examenes ni "
                "entregas. No afirmes que el usuario no tiene examenes o entregas."
            )
        notes.append("Las entregas de PoliformaT no estan disponibles en esta version (v0).")
        if self._last_refresh_failed:
            notes.append("No se pudo contactar con la UPV; estos datos son de la cache local.")
        return " ".join(notes) if notes else None
