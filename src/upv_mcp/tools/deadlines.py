"""Tool `list_upcoming_deadlines`: fechas limite proximas.

Sobre la cobertura real en la v0: el calendario de horarios de la UPV contiene solo
clases, y las entregas viven en PoliformaT, que no esta integrado todavia. Por eso
la tool existe con su forma definitiva pero puede devolver lista vacia. La
alternativa -- no exponerla, o exponerla sin avisar -- era peor: sin aviso, el
modelo concluye "no tienes nada pendiente", que es una respuesta falsa y que el
estudiante se puede creer.
"""

from __future__ import annotations

from upv_mcp.models import DeadlinesResult
from upv_mcp.repository import AcademicRepository
from upv_mcp.tools.common import build_meta, horizon_bounds

DESCRIPTION = """\
Devuelve las FECHAS LIMITE proximas (examenes y entregas) dentro de los siguientes \
N dias, ordenadas de la mas cercana a la mas lejana.

USALA para:
- "que examenes tengo pronto"
- "tengo alguna entrega esta semana"
- "que se me viene encima"
- "cuando es mi proximo examen"

NO LA USES:
- Para clases normales -> usa get_schedule (si hay fechas) o get_next_class (si es \
lo inmediato). Una clase no es una fecha limite y esta tool no las devuelve.
- Si el usuario da un rango con inicio y fin concretos -> usa get_schedule.

LIMITACION IMPORTANTE DE ESTA VERSION, leela antes de responder: este servidor solo \
esta conectado al calendario de HORARIOS de la UPV, que contiene clases pero NO \
examenes, y la integracion con PoliformaT (donde viven las entregas) todavia no \
existe. Por eso esta tool puede devolver una lista VACIA aunque el estudiante si \
tenga examenes o entregas pendientes.

Consulta siempre meta.coverage_note y traslada esa limitacion al usuario con \
claridad: di que NO PUEDES VER sus examenes y entregas y de donde tendria que \
sacarlos. NO digas ni sugieras que no tiene ninguno.\
"""

#: Horizonte por defecto. Dos semanas cubre la pregunta tipica sin inundar contexto.
DEFAULT_DAYS_AHEAD = 14
MAX_DAYS_AHEAD = 365


async def list_upcoming_deadlines(
    repo: AcademicRepository,
    days_ahead: int = DEFAULT_DAYS_AHEAD,
    *,
    limit: int,
) -> DeadlinesResult:
    """Implementacion. `server.py` la envuelve y le pone la descripcion MCP."""
    if days_ahead < 1 or days_ahead > MAX_DAYS_AHEAD:
        raise ValueError(f"days_ahead debe estar entre 1 y {MAX_DAYS_AHEAD}, no {days_ahead}.")

    await repo.ensure_fresh()
    start, end = horizon_bounds(repo, days_ahead)

    total = repo.cache.count_assignments_between(start, end)
    deadlines = repo.cache.assignments_between(start, end, limit=limit)

    return DeadlinesResult(
        horizon_days=days_ahead,
        deadlines=deadlines,
        meta=build_meta(repo, total_matching=total, returned=len(deadlines)),
    )
