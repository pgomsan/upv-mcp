"""Tool `list_upcoming_deadlines`: fechas limite proximas.

Sobre la cobertura real en la v0: el calendario de horarios de la UPV contiene solo
clases, y las entregas viven en PoliformaT, que no esta integrado todavia. Por eso
la tool existe con su forma definitiva pero puede devolver lista vacia. La
alternativa -- no exponerla, o exponerla sin avisar -- era peor: sin aviso, el
modelo concluye "no tienes nada pendiente", que es una respuesta falsa y que el
estudiante se puede creer.
"""

from __future__ import annotations

from upv_mcp.models import DeadlinesResult, SubmissionStatus
from upv_mcp.repository import AcademicRepository
from upv_mcp.tools.common import build_meta, horizon_bounds

DESCRIPTION = """\
Devuelve las ENTREGAS de PoliformaT con fecha limite proxima, de la mas cercana a \
la mas lejana, con su estado de entrega y su nota si ya esta corregida.

USALA para: "tengo alguna entrega esta semana", "que me queda por entregar", \
"que se me viene encima", "me han puesto ya la nota de la practica 2".

NO LA USES:
- Para clases -> usa get_schedule (con fechas) o get_next_class (lo inmediato). \
Una clase no es una fecha limite y esta tool no las devuelve.
- Para avisos de profesores -> usa list_announcements.

CAMPO `submission` de cada entrega:
- "submitted": entregada (trae submitted_at y late).
- "not_submitted": consta que NO esta entregada.
- "unknown": PoliformaT no da el dato. NO es lo mismo que no entregada: di que no \
lo sabes, nunca que le falta entregarla.
- Si graded es true, trae grade y grade_max, con coma decimal ("8,30" de "10,00"), \
y feedback con el comentario del profesor.

PARAMETROS:
- days_ahead: dias hacia adelante. Por defecto 14.
- days_back: dias hacia ATRAS. Por defecto 0, que es el caso normal. Usalo solo si \
preguntan por algo YA PASADO ("que entregue en Vision por Computador"). Incluir el \
pasado sin que lo pidan entierra lo que importa.
- pending_only: solo lo que consta como no entregado. Para "que tengo sin hacer". \
Lo de estado unknown queda fuera de este filtro a proposito.
- course: filtra por asignatura. Acepta codigo ("14537"), nombre o parte de el \
("vision") o siglas ("VC"). Usalo cuando nombren una asignatura.

EXAMENES: NO tienes acceso al calendario de examenes de la UPV. En PoliformaT hay \
tareas tituladas "Examen" que no lo son, y examenes que no son tarea, asi que no se \
clasifican. Si preguntan por examenes, di que no puedes verlos; NO deduzcas que no \
tiene ninguno.

Lee meta.coverage_note antes de responder. Una lista vacia con days_back=0 significa \
que no hay entregas proximas, normal en vacaciones o entre cursos.\
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
    pending_only: bool = False,
    course: str | None = None,
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

    if pending_only:
        # `is_pending` solo es cierto para lo que consta explicitamente como NO
        # entregado: lo desconocido no se cuela como pendiente.
        todas = repo.cache.assignments_between(start, end, course=course)
        filtradas = [a for a in todas if a.is_pending]
        total = len(filtradas)
        deadlines = filtradas[:limit]
    else:
        total = repo.cache.count_assignments_between(start, end, course=course)
        deadlines = repo.cache.assignments_between(start, end, course=course, limit=limit)

    desconocidas = sum(
        1
        for a in deadlines
        if a.submission is None or a.submission.status is SubmissionStatus.UNKNOWN
    )
    nota = (
        f"{desconocidas} de las entregas listadas no traen estado de entrega: no se "
        "sabe si estan hechas. No las des por pendientes ni por entregadas."
        if desconocidas
        else None
    )

    return DeadlinesResult(
        horizon_days=days_ahead,
        days_back=days_back,
        pending_only=pending_only,
        deadlines=deadlines,
        meta=build_meta(repo, total_matching=total, returned=len(deadlines), extra_note=nota),
    )
