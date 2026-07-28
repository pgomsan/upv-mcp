"""Tool `get_schedule`: clases y examenes en un rango de fechas."""

from __future__ import annotations

from datetime import date

from upv_mcp.models import ScheduleResult
from upv_mcp.repository import AcademicRepository
from upv_mcp.tools.common import build_meta, day_bounds

DESCRIPTION = """\
Devuelve las clases y examenes del estudiante en un RANGO DE FECHAS CONCRETO, \
ordenados cronologicamente, con asignatura, aula, edificio, hora de inicio y fin, \
docente, tipo de docencia (TA/TS/PL) y grupo.

USALA cuando el usuario mencione un dia, una semana o un periodo:
- "que clases tengo el martes"
- "mi horario de la semana que viene"
- "tengo algo el 15 de octubre por la tarde"
- "cuantas clases me quedan este mes"

NO LA USES:
- Si el usuario solo quiere saber cual es su PROXIMA clase ("que tengo ahora", \
"donde voy despues") -> usa get_next_class. Esta tool no sabe cual es "la \
siguiente": devuelve un rango entero y tendrias que deducirlo tu.
- Si pregunta por examenes o entregas proximos sin acotar fechas -> usa \
list_upcoming_deadlines.

FECHAS: absolutas y en formato YYYY-MM-DD. Resuelve tu las expresiones relativas \
("manana", "la semana que viene", "el jueves") antes de llamar. El campo \
meta.generated_at de la respuesta lleva la fecha y hora actuales del servidor; \
usalo para comprobar tus calculos si tienes dudas sobre que dia es hoy.

El rango es INCLUSIVO por ambos extremos y se interpreta en hora local de Valencia. \
Para un solo dia, pon la misma fecha en start_date y end_date.

`course` filtra por asignatura: acepta el codigo UPV ("14537"), el nombre o parte \
de el ("Vision por Computador", "vision") o las siglas ("VC"). Usalo cuando el \
usuario nombre una asignatura, en vez de pedirlo todo y filtrar tu.

Si hay mas resultados de los que caben, la respuesta viene recortada y \
meta.truncated vale true: en ese caso dilo explicitamente y sugiere acotar el \
rango. Nunca presentes una lista recortada como si fuera completa.

Una lista vacia significa que no hay clases en ese rango (festivo, vacaciones o \
fin de curso), no que haya fallado la consulta.\
"""


async def get_schedule(
    repo: AcademicRepository,
    start_date: date,
    end_date: date,
    course: str | None = None,
    *,
    limit: int,
) -> ScheduleResult:
    """Implementacion. `server.py` la envuelve y le pone la descripcion MCP."""
    if end_date < start_date:
        raise ValueError(
            f"end_date ({end_date}) es anterior a start_date ({start_date}). "
            "El rango debe ir de menor a mayor."
        )

    await repo.ensure_fresh()
    start, end = day_bounds(repo, start_date, end_date)

    total = repo.cache.count_sessions_between(start, end, course=course)
    sessions = repo.cache.sessions_between(start, end, course=course, limit=limit)

    # Un filtro que no encaja con nada devuelve vacio, y sin aviso parece que no hay
    # clases ese dia en vez de que la asignatura no existe.
    nota = None
    if course and total == 0:
        conocidas = ", ".join(sorted(c.name for c in repo.cache.courses_by_code().values()))
        nota = (
            f"Ninguna asignatura coincide con '{course}'. Tus asignaturas son: "
            f"{conocidas}. No afirmes que no hay clases sin comprobar el nombre."
        )

    return ScheduleResult(
        range_start=start,
        range_end=end,
        sessions=sessions,
        meta=build_meta(repo, total_matching=total, returned=len(sessions), extra_note=nota),
    )
