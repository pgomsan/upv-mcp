"""Utilidades compartidas por las tools.

INVARIANTE DE ARQUITECTURA: `tools/` no importa nada de `mcp`, ni de httpx2, ni de
icalendar. Solo modelos de dominio y el repositorio. Quien conecta esto con el SDK
es `server.py`, y es el unico sitio que lo hace.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from upv_mcp.models import ResultMeta
from upv_mcp.repository import AcademicRepository


def day_bounds(
    repo: AcademicRepository, start: date, end: date
) -> tuple[datetime, datetime]:
    """Convierte un rango de fechas inclusivo en un rango de instantes locales.

    El dia final se incluye entero: pedir 12/09 a 12/09 debe devolver las clases de
    ese dia, no una lista vacia.
    """
    tz = repo.timezone
    return (
        datetime.combine(start, time.min, tzinfo=tz),
        datetime.combine(end, time.max, tzinfo=tz),
    )


def horizon_bounds(
    repo: AcademicRepository, days_ahead: int, *, days_back: int = 0
) -> tuple[datetime, datetime]:
    """Ventana desde ahora hasta dentro de N dias.

    `days_back` la extiende hacia atras. Por defecto 0: la pregunta habitual es
    sobre lo que queda por hacer, no sobre lo ya entregado.
    """
    now = repo.now()
    return now - timedelta(days=days_back), now + timedelta(days=days_ahead)


def build_meta(
    repo: AcademicRepository,
    *,
    total_matching: int,
    returned: int,
    extra_note: str | None = None,
) -> ResultMeta:
    """Construye los metadatos de una respuesta.

    `coverage_note` viaja en todas las respuestas a proposito: es lo que impide que
    el modelo concluya "no tienes examenes" cuando en realidad no los esta mirando.
    """
    notes = [n for n in (repo.coverage_note(), extra_note) if n]
    return ResultMeta(
        total_matching=total_matching,
        returned=returned,
        truncated=returned < total_matching,
        stale=repo.serving_stale,
        generated_at=repo.now(),
        coverage_note=" ".join(notes) if notes else None,
    )
