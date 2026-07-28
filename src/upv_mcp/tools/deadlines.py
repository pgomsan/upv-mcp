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

PARAMETROS:
- days_ahead: cuantos dias hacia adelante mirar. Por defecto 14.
- days_back: cuantos dias hacia ATRAS incluir. Por defecto 0, y ese es el caso \
normal: casi siempre se pregunta por lo que queda por hacer. Usalo solo si el \
usuario pregunta explicitamente por algo YA PASADO ("que entregue en Vision por \
Computador", "cuando era la practica 3", "que entregas hubo en mayo"). Si dudas, \
dejalo en 0: incluir el pasado sin que lo pidan llena la respuesta de entregas \
cerradas y entierra lo que de verdad importa.

EXAMENES: este servidor NO tiene acceso al calendario de examenes de la UPV. En \
PoliformaT algunos examenes aparecen como tarea y otros no, y hay tareas tituladas \
"Examen" que no lo son, asi que NO se clasifican: todo lo que devuelve esta tool \
son entregas. Si el usuario pregunta por examenes, di que no puedes verlos y que \
lo consulte en la web de su titulacion. NO deduzcas que no tiene examenes.

Consulta siempre meta.coverage_note antes de responder y traslada al usuario \
cualquier limitacion que indique. Una lista vacia con days_back=0 significa que no \
hay entregas proximas, lo cual es normal en vacaciones o entre cursos.\
"""

#: Horizonte por defecto. Dos semanas cubre la pregunta tipica sin inundar contexto.
DEFAULT_DAYS_AHEAD = 14
MAX_DAYS_AHEAD = 365

#: Mirar atras es la excepcion, no lo normal: por defecto 0.
MAX_DAYS_BACK = 365


async def list_upcoming_deadlines(
    repo: AcademicRepository,
    days_ahead: int = DEFAULT_DAYS_AHEAD,
    days_back: int = 0,
    *,
    limit: int,
) -> DeadlinesResult:
    """Implementacion. `server.py` la envuelve y le pone la descripcion MCP."""
    if days_ahead < 1 or days_ahead > MAX_DAYS_AHEAD:
        raise ValueError(f"days_ahead debe estar entre 1 y {MAX_DAYS_AHEAD}, no {days_ahead}.")
    if days_back < 0 or days_back > MAX_DAYS_BACK:
        raise ValueError(f"days_back debe estar entre 0 y {MAX_DAYS_BACK}, no {days_back}.")

    await repo.ensure_fresh()
    start, end = horizon_bounds(repo, days_ahead, days_back=days_back)

    total = repo.cache.count_assignments_between(start, end)
    deadlines = repo.cache.assignments_between(start, end, limit=limit)

    return DeadlinesResult(
        horizon_days=days_ahead,
        days_back=days_back,
        deadlines=deadlines,
        meta=build_meta(repo, total_matching=total, returned=len(deadlines)),
    )
