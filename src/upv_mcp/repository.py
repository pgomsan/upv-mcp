"""Orquestacion entre fuentes y cache.

Es la pieza que las tools usan. Decide cuando refrescar, y garantiza que un fallo
de red degrade a cache antigua en vez de dejar al usuario sin respuesta.

INVARIANTE: no importa nada de `mcp`. Las tools reciben este objeto ya construido.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from zoneinfo import ZoneInfo

from upv_mcp.cache.db import CacheRepository
from upv_mcp.config import Settings
from upv_mcp.models import Assignment
from upv_mcp.sources.base import SourceError
from upv_mcp.sources.ics import IcsSource
from upv_mcp.sources.poliformat import PoliformatSource


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
        try:
            payload = await self._poliformat.fetch()
        except (SourceError, OSError):
            self._poliformat_failed = True
            return

        self._poliformat_failed = False
        self._cache.replace_calendar(
            "poliformat",
            [],
            self._nombres_reales(payload.assignments),
            fetched_at=payload.fetched_at,
        )

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
        elif self._poliformat_failed:
            notes.append(
                "No se pudo consultar PoliformaT en este refresco, asi que puede haber "
                "entregas que no aparezcan."
            )

        if self._last_refresh_failed:
            notes.append("No se pudo contactar con la UPV; estos datos son de la cache local.")
        return " ".join(notes) if notes else None
