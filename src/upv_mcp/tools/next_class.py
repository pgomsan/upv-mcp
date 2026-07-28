"""Tool `get_next_class`: la siguiente sesion a partir de este momento."""

from __future__ import annotations

from upv_mcp.models import NextClassResult
from upv_mcp.repository import AcademicRepository
from upv_mcp.tools.common import build_meta

DESCRIPTION = """\
Devuelve UNICAMENTE la siguiente sesion que empieza despues de este preciso \
momento, con su asignatura, aula, edificio, hora de inicio y fin, docente, y \
cuantos minutos faltan para que empiece.

USALA para preguntas sobre lo inmediato, sin fecha:
- "cual es mi proxima clase"
- "donde tengo que ir ahora"
- "a que hora empiezo hoy"
- "en que aula estoy la siguiente hora"
- "me da tiempo a comer"

NO LA USES:
- Si el usuario menciona un dia o un rango ("manana", "el jueves", "esta semana", \
"el 15 de octubre") -> usa get_schedule. Esta tool IGNORA cualquier fecha: siempre \
parte del instante actual, asi que no sirve para consultar otro dia.
- Si pregunta por examenes o entregas -> usa list_upcoming_deadlines.

No admite parametros: no le pases fechas.

Si no queda ninguna clase por delante (vacaciones, fin de curso, horario no \
publicado todavia), devuelve session = null. En ese caso di que no hay mas clases \
programadas en el calendario; NO te inventes una ni asumas que hubo un error.\
"""


async def get_next_class(repo: AcademicRepository) -> NextClassResult:
    """Implementacion. `server.py` la envuelve y le pone la descripcion MCP."""
    await repo.ensure_fresh()

    now = repo.now()
    session = repo.cache.next_session_after(now)

    starts_in = None
    if session is not None:
        starts_in = max(0, int((session.start - now).total_seconds() // 60))

    note = (
        None
        if session is not None
        else "No queda ninguna clase futura en el calendario descargado."
    )
    return NextClassResult(
        session=session,
        starts_in_minutes=starts_in,
        meta=build_meta(
            repo,
            total_matching=1 if session else 0,
            returned=1 if session else 0,
            extra_note=note,
        ),
    )
