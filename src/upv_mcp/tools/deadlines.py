"""Tool `list_upcoming_deadlines`: fechas limite proximas.

Junta dos origenes: las entregas de PoliformaT y los examenes del iCal oficial.

Toda la tool gira alrededor de una distincion: **"no hay nada" y "no lo se" no son
lo mismo**, y confundirlos produce respuestas falsas que el estudiante se cree. Se
traduce en tres avisos distintos en `meta.coverage_note`:

* Sin calendario de examenes configurado -> no se ven, y hay que decirlo.
* Con datos que no llegan a la fecha preguntada (el calendario se publica por curso,
  asi que en verano no cubre septiembre) -> vacio significa "aun no publicado".
* Con entregas sin estado -> no se sabe si estan hechas.
"""

from __future__ import annotations

from upv_mcp.models import DeadlinesResult, EventKind, SubmissionStatus
from upv_mcp.repository import AcademicRepository
from upv_mcp.tools.common import build_meta, horizon_bounds

DESCRIPTION = """\
Devuelve las FECHAS LIMITE proximas (entregas de PoliformaT y examenes), de mas \
cercana a mas lejana, con estado de entrega y nota si esta corregida.

USALA para: "tengo alguna entrega esta semana", "cuando es mi proximo examen", \
"me han puesto ya la nota de la practica 2".

NO LA USES:
- Para clases -> usa get_schedule (con fechas) o get_next_class (lo inmediato). \
Una clase no es una fecha limite.
- Para examenes de un tramo concreto ("examenes en junio") -> usa get_schedule: \
esta mira desde HOY hacia adelante, no un rango cualquiera.
- Para la NOTA de una asignatura ("que nota tengo en Estadistica") -> NINGUNA tool: \
el expediente no se expone. Aqui solo hay notas de tareas corregidas, que no son la \
nota de la asignatura: dilo, no des una por la otra.
- Para avisos de profesores -> usa list_announcements.

`submission` de cada entrega:
- "submitted": entregada (trae submitted_at y late).
- "not_submitted": consta que NO esta entregada.
- "unknown": PoliformaT no da el dato. NO es lo mismo que no entregada: di que no \
lo sabes, nunca que le falta entregarla.
- graded true: trae grade y grade_max con coma decimal ("8,30" de "10,00") y \
feedback del profesor.

PARAMETROS:
- days_ahead: dias hacia adelante. Por defecto 14.
- days_back: dias hacia ATRAS. Por defecto 0. Usalo solo si preguntan por algo YA \
PASADO ("que entregue en Vision"). Incluirlo sin pedirlo entierra lo que importa.
- pending_only: solo lo que consta como no entregado ("que tengo sin hacer"). Lo \
unknown queda fuera a proposito.
- course: por asignatura: codigo ("14537"), nombre o parte ("vision"), siglas ("VC").

EXAMENES: cada elemento trae `kind` ("exam" o "assignment"). Salen de un calendario \
aparte OPCIONAL: si no esta configurado no habra ninguno y meta.coverage_note lo \
dira. Las tareas de PoliformaT nunca se cuentan como examen aunque se titulen asi.

Lee meta.coverage_note antes de responder: si avisa de algo, trasladalo y NO \
deduzcas que no le falta nada. Si no avisa, la lista esta completa.\
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

    # "No hay nada" y "los datos no llegan hasta ahi" son cosas MUY distintas para
    # un estudiante. El calendario de examenes se publica por curso academico, asi
    # que en verano no cubre septiembre y una lista vacia parece tranquilizadora
    # cuando en realidad no se sabe.
    horizonte = repo.cache.latest_known_due()
    aviso_horizonte = None
    if horizonte is not None and end > horizonte:
        aviso_horizonte = (
            f"Los datos publicados llegan hasta el {horizonte:%Y-%m-%d}. Mas alla de "
            "esa fecha NO hay informacion todavia, asi que una lista vacia no "
            "significa que no haya nada: significa que aun no se ha publicado."
        )

    # Solo las ENTREGAS pueden tener estado desconocido. Un examen del .ics nunca
    # trae submission y no le falta ningun dato: contarlo aqui avisaba de "10
    # entregas sin estado" cuando eran 10 examenes.
    desconocidas = sum(
        1
        for a in deadlines
        if a.kind is EventKind.ASSIGNMENT
        and (a.submission is None or a.submission.status is SubmissionStatus.UNKNOWN)
    )
    avisos = [aviso_horizonte] if aviso_horizonte else []
    if desconocidas:
        avisos.append(
            f"{desconocidas} de las entregas listadas no traen estado de entrega: no "
            "se sabe si estan hechas. No las des por pendientes ni por entregadas."
        )
    # Mismo cuidado que en get_schedule: "esa asignatura no es tuya" y "esa asignatura
    # no tiene entregas aqui" no son la misma frase.
    if course and total == 0:
        encajan = repo.cache.matching_course_names(course)
        if not encajan:
            conocidas = ", ".join(repo.cache.known_course_names())
            avisos.append(
                f"Ninguna asignatura tuya se llama '{course}'. Tus asignaturas son: {conocidas}."
            )
        else:
            avisos.append(
                f"{', '.join(encajan)} SI es una asignatura tuya, pero no tiene ninguna "
                "fecha limite en el periodo consultado. NO digas que la asignatura no "
                "es suya."
            )
    nota = " ".join(avisos) if avisos else None

    return DeadlinesResult(
        horizon_days=days_ahead,
        days_back=days_back,
        pending_only=pending_only,
        deadlines=deadlines,
        meta=build_meta(repo, total_matching=total, returned=len(deadlines), extra_note=nota),
    )
