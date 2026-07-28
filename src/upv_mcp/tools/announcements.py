"""Tool `list_announcements`: avisos publicados por los profesores."""

from __future__ import annotations

from datetime import timedelta

from upv_mcp.models import AnnouncementsResult
from upv_mcp.repository import AcademicRepository
from upv_mcp.tools.common import build_meta

DESCRIPTION = """\
Devuelve los AVISOS que los profesores han publicado en PoliformaT, del mas \
reciente al mas antiguo, con asignatura, titulo, autor, fecha y texto.

USALA cuando el usuario pregunte por comunicaciones de sus profesores:
- "ha dicho algo el profe de Redes"
- "hay algun aviso nuevo"
- "me he perdido algo esta semana"
- "que ha publicado en PoliformaT"

NO LA USES:
- Para clases o horarios -> usa get_schedule o get_next_class.
- Para fechas de entrega -> usa list_upcoming_deadlines. Un aviso puede MENCIONAR \
una entrega, pero la fecha fiable esta en la otra tool.
- Para apuntes, transparencias o enunciados -> son MCP resources, no una tool: \
mira los recursos `upv://materiales/...`.

PARAMETROS: days_back limita a los avisos de los ultimos N dias (por defecto 30). \
Subelo si el usuario pregunta por algo mas antiguo.

El texto viene recortado a 1200 caracteres. Si un aviso parece incompleto y \
necesitas el original, la respuesta trae su `url`.

Solo cubre las asignaturas del curso academico en marcha. Una lista vacia \
significa que no hay avisos en ese periodo, no que haya fallado nada.\
"""

DEFAULT_DAYS_BACK = 30
MAX_DAYS_BACK = 365


async def list_announcements(
    repo: AcademicRepository,
    days_back: int = DEFAULT_DAYS_BACK,
    *,
    limit: int,
) -> AnnouncementsResult:
    """Implementacion. `server.py` la envuelve y le pone la descripcion MCP."""
    if days_back < 1 or days_back > MAX_DAYS_BACK:
        raise ValueError(f"days_back debe estar entre 1 y {MAX_DAYS_BACK}, no {days_back}.")

    await repo.ensure_fresh()
    desde = repo.now() - timedelta(days=days_back)

    total = repo.cache.count_announcements(since=desde)
    avisos = repo.cache.announcements(since=desde, limit=limit)

    return AnnouncementsResult(
        announcements=avisos,
        meta=build_meta(repo, total_matching=total, returned=len(avisos)),
    )
