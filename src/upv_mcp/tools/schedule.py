"""Tool `get_schedule`: clases y examenes en un rango de fechas."""

from __future__ import annotations

from datetime import date

from upv_mcp.models import EventKind, ScheduleResult
from upv_mcp.repository import AcademicRepository
from upv_mcp.tools.common import build_meta, day_bounds

DESCRIPTION = """\
Devuelve las clases y examenes del estudiante en un RANGO DE FECHAS CONCRETO, \
ordenados cronologicamente, con asignatura, aula, edificio, hora de inicio y fin, \
docente, tipo de docencia (TA/TS/PL) y grupo.

USALA cuando el usuario mencione un dia, una semana o un periodo:
- "que clases tengo el martes"
- "mi horario de la semana que viene"
- "tengo examenes en junio": los examenes de un tramo concreto salen AQUI.
- "quien da Interfaces": el docente viene en cada sesion. Pide UN rango amplio con \
`course`; no vayas tanteando rangos sueltos a ver si aparece.

NO LA USES:
- Si el usuario solo quiere saber cual es su PROXIMA clase ("que tengo ahora", \
"donde voy despues") -> usa get_next_class. Esta devuelve el rango entero y \
tendrias que deducir tu cual es la siguiente.
- Si pregunta por examenes o entregas proximos SIN acotar fechas ("cuando es mi \
proximo examen") -> usa list_upcoming_deadlines.

FECHAS: absolutas y en formato YYYY-MM-DD. Resuelve tu las expresiones relativas \
("manana", "la semana que viene", "el jueves") antes de llamar. meta.generated_at \
trae la fecha actual del servidor; usalo si dudas de que dia es hoy.

El rango es INCLUSIVO por ambos extremos y se interpreta en hora local de Valencia. \
Para un solo dia, pon la misma fecha en start_date y end_date.

`course` filtra por asignatura: acepta codigo ("14537"), nombre o parte de el \
("vision") o siglas ("VC"). Usalo cuando el usuario nombre una asignatura, en vez \
de pedirlo todo y filtrar tu.

Si hay mas resultados de los que caben, la respuesta viene recortada y \
meta.truncated vale true: en ese caso dilo explicitamente y sugiere acotar el \
rango. Nunca presentes una lista recortada como si fuera completa.

Una lista vacia no es un fallo, pero puede ser "no hay nada ese dia" (festivo, \
vacaciones) o "el calendario no llega hasta ahi". Lo dice meta.coverage_note: si \
avisa de que el horario o los examenes acaban antes de la fecha pedida, \
trasladalo y NO afirmes que no hay nada.\
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

    # Un filtro que no devuelve nada tiene DOS causas que no se pueden confundir: que
    # esa asignatura no sea suya, o que si lo sea y no tenga clases en estas fechas.
    # Decir lo primero cuando pasa lo segundo le niega al estudiante una asignatura
    # que cursa, asi que cada caso lleva su aviso.
    avisos: list[str] = []
    if course and total == 0:
        encajan = repo.cache.matching_course_names(course)
        if not encajan:
            conocidas = ", ".join(repo.cache.known_course_names())
            avisos.append(
                f"Ninguna asignatura tuya se llama '{course}'. Tus asignaturas son: "
                f"{conocidas}. No afirmes que no hay clases sin comprobar el nombre."
            )
        else:
            cuales = ", ".join(encajan)
            avisos.append(
                f"{cuales} SI es una asignatura tuya, pero no tiene ninguna clase "
                f"entre {start_date} y {end_date}. Di que no hay clases en esas "
                "fechas; NO digas que la asignatura no es suya ni ofrezcas otra en "
                "su lugar."
            )

    # Esta tool sirve DOS calendarios que no llegan igual de lejos: las clases se
    # publican por curso y los examenes vienen de su propio .ics. Un rango que se
    # pasa del horizonte de uno de los dos devuelve resultados a medias sin que se
    # note, y esa es la forma mas creible de mentir que tiene el servidor: contestar
    # con las clases de octubre y ningun examen se lee como "no tienes examenes".
    #
    # Cada aviso dice tambien lo que SI esta cubierto. Sin esa mitad, el modelo
    # comprime "el calendario de examenes" en "el calendario" y acaba negando unas
    # clases que si estan publicadas: pasó en la ronda con subagentes, con el aviso
    # anterior, que solo nombraba lo que faltaba.
    #
    # La comparacion es por DIA: el rango llega a las 23:59 y el ultimo examen puede
    # ser a las 11:00, asi que comparar instantes hace saltar el aviso el mismo dia
    # en que los datos todavia alcanzan.
    horizonte_clases = repo.cache.latest_known_session(EventKind.CLASS)
    horizonte_examenes = repo.cache.latest_known_session(EventKind.EXAM)

    if horizonte_clases is not None and end.date() > horizonte_clases.date():
        avisos.append(
            f"El horario de clases publicado acaba el {horizonte_clases:%Y-%m-%d} y has "
            "preguntado mas alla. Lo que falte a partir de esa fecha puede estar sin "
            "publicar todavia; no lo des por inexistente."
        )
    if horizonte_examenes is not None and end.date() > horizonte_examenes.date():
        cubierto = (
            f"Las CLASES si estan publicadas hasta el {horizonte_clases:%Y-%m-%d}. "
            if horizonte_clases is not None and horizonte_clases > horizonte_examenes
            else ""
        )
        avisos.append(
            f"{cubierto}El calendario de EXAMENES acaba el {horizonte_examenes:%Y-%m-%d}: "
            "las fechas del curso siguiente todavia no estan determinadas, y apareceran "
            "solas en el iCal cuando se fijen. Si preguntan por examenes, di eso; no "
            "digas que no hay ninguno, ni que faltan las clases."
        )

    return ScheduleResult(
        range_start=start,
        range_end=end,
        sessions=sessions,
        meta=build_meta(
            repo,
            total_matching=total,
            returned=len(sessions),
            extra_note=" ".join(avisos) if avisos else None,
        ),
    )
