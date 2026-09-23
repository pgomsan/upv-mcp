"""Entrypoint MCP: registra las tools sobre el SDK oficial y arranca por stdio.

Este es el UNICO modulo que importa `mcp`. `tools/`, `sources/`, `cache/` y
`repository.py` no saben que existe MCP, lo que permite anadir PoliformaT en la v1
tocando solo la capa de fuentes.

SDK: `mcp` 2.x, donde la clase se llama `MCPServer` (era `FastMCP` en la 1.x) y el
Context se recibe por inyeccion de parametro, no con un get_context() ambiental.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date

from mcp.server.mcpserver import Context, MCPServer

from upv_mcp import __version__
from upv_mcp.cache.db import CacheRepository
from upv_mcp.config import Settings, load_settings
from upv_mcp.models import (
    AnnouncementsResult,
    DeadlinesResult,
    MaterialContentResult,
    NextClassResult,
    ScheduleResult,
)
from upv_mcp.repository import AcademicRepository
from upv_mcp.tools import announcements as announcements_tool
from upv_mcp.tools import deadlines as deadlines_tool
from upv_mcp.tools import materials as materials_tool
from upv_mcp.tools import next_class as next_class_tool
from upv_mcp.tools import schedule as schedule_tool

INSTRUCTIONS = """\
Calendario academico de un estudiante de la UPV (Universitat Politecnica de \
Valencia). Todas las horas son locales de Valencia (Europe/Madrid).

Cubre el horario de clases y los examenes, de los calendarios .ics de la UPV, y \
PoliformaT: entregas con su estado y su nota, avisos de los profesores y materiales \
de cada asignatura.

Elige la tool por el tipo de pregunta: get_next_class para lo inmediato sin fecha, \
get_schedule cuando haya un dia o un rango, list_upcoming_deadlines para fechas \
limite (entregas y examenes), list_announcements para lo que publican los \
profesores, y read_material para leer un fichero concreto. Para saber QUE materiales \
hay, los resources `upv://materiales`, no una tool.

Lo que NO hay: las notas del expediente. Solo se ven las de las tareas ya corregidas \
en PoliformaT, con su escala.

Revisa siempre meta.coverage_note antes de afirmar que algo no existe. Una lista \
vacia puede significar "no hay nada" o "los datos no llegan hasta ahi", y el aviso \
dice cual de las dos: el calendario de examenes y PoliformaT son opcionales, y el \
calendario academico se publica por curso, asi que en verano todavia no cubre \
septiembre.\
"""


@dataclass
class AppContext:
    """Dependencias vivas durante toda la sesion del servidor."""

    settings: Settings
    repo: AcademicRepository


@asynccontextmanager
async def app_lifespan(server: MCPServer) -> AsyncIterator[AppContext]:
    """Abre configuracion y cache al arrancar, y las cierra al terminar.

    La configuracion se resuelve aqui y no a nivel de modulo para que un fallo
    (falta la URL iCal) se manifieste al arrancar el servidor, con un mensaje util,
    en vez de dentro de la primera llamada a una tool.
    """
    global _app
    settings = load_settings()
    settings.ensure_dirs()
    cache = CacheRepository(settings.db_path, settings.timezone)
    contexto = AppContext(settings=settings, repo=AcademicRepository(settings, cache))
    _app = contexto
    try:
        yield contexto
    finally:
        _app = None
        cache.close()


#: El SDK no permite inyectar Context en un resource SIN variables en la URI
#: ("Context injection for static resources is not supported"), asi que el indice
#: de materiales necesita esta referencia. Los resources con plantilla y las tools
#: siguen usando la inyeccion normal.
_app: AppContext | None = None


def _current_app() -> AppContext:
    if _app is None:  # pragma: no cover - solo si se usa fuera del servidor
        raise RuntimeError("El servidor no esta inicializado.")
    return _app


mcp = MCPServer(
    name="upv-mcp",
    title="UPV - Calendario academico",
    instructions=INSTRUCTIONS,
    version=__version__,
    lifespan=app_lifespan,
)


@mcp.tool(
    title="Horario en un rango de fechas",
    description=schedule_tool.DESCRIPTION,
)
async def get_schedule(
    start_date: date,
    end_date: date,
    ctx: Context[AppContext],
    course: str | None = None,
) -> ScheduleResult:
    app = ctx.request_context.lifespan_context
    return await schedule_tool.get_schedule(
        app.repo, start_date, end_date, course, limit=app.settings.max_results
    )


@mcp.tool(
    title="Proxima clase",
    description=next_class_tool.DESCRIPTION,
)
async def get_next_class(ctx: Context[AppContext]) -> NextClassResult:
    app = ctx.request_context.lifespan_context
    return await next_class_tool.get_next_class(app.repo)


@mcp.tool(
    title="Fechas limite proximas",
    description=deadlines_tool.DESCRIPTION,
)
async def list_upcoming_deadlines(
    ctx: Context[AppContext],
    days_ahead: int = deadlines_tool.DEFAULT_DAYS_AHEAD,
    days_back: int = 0,
    pending_only: bool = False,
    course: str | None = None,
) -> DeadlinesResult:
    app = ctx.request_context.lifespan_context
    return await deadlines_tool.list_upcoming_deadlines(
        app.repo,
        days_ahead,
        days_back,
        pending_only,
        course,
        limit=app.settings.max_results,
    )


@mcp.tool(
    title="Avisos de los profesores",
    description=announcements_tool.DESCRIPTION,
)
async def list_announcements(
    ctx: Context[AppContext],
    days_back: int = announcements_tool.DEFAULT_DAYS_BACK,
) -> AnnouncementsResult:
    app = ctx.request_context.lifespan_context
    return await announcements_tool.list_announcements(
        app.repo, days_back, limit=app.settings.max_results
    )


@mcp.tool(
    title="Leer un material",
    description=materials_tool.READ_DESCRIPTION,
)
async def read_material(
    course: str,
    file: str,
    ctx: Context[AppContext],
) -> MaterialContentResult:
    return await materials_tool.read_material(
        ctx.request_context.lifespan_context.repo, course, file
    )


# --------------------------------------------------------------------------------------
# Resources para NAVEGAR los materiales: listan sin descargar. Leer el contenido de
# un fichero concreto es la tool read_material, porque descargarlo y convertirlo es
# una accion con coste, no algo que se consulte de pasada.
# --------------------------------------------------------------------------------------


@mcp.resource(
    materials_tool.INDEX_URI,
    title="Materiales por asignatura",
    description="Indice de las asignaturas con apuntes y enunciados en PoliformaT.",
    mime_type="text/markdown",
)
async def materiales_index() -> str:
    return await materials_tool.render_index(_current_app().repo)


@mcp.resource(
    materials_tool.COURSE_URI_TEMPLATE,
    title="Materiales de una asignatura",
    description="Apuntes, enunciados y enlaces de una asignatura, por codigo UPV "
    "(p.ej. 14541). Devuelve metadatos y URL, no el contenido de los ficheros.",
    mime_type="text/markdown",
)
async def materiales_de_asignatura(course_code: str, ctx: Context[AppContext]) -> str:
    return await materials_tool.render_course(
        ctx.request_context.lifespan_context.repo, course_code
    )


def main() -> None:
    """Arranca el servidor por stdio (transporte por defecto en el SDK)."""
    mcp.run()


if __name__ == "__main__":  # pragma: no cover
    main()
