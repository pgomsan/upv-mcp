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
from upv_mcp.models import DeadlinesResult, NextClassResult, ScheduleResult
from upv_mcp.repository import AcademicRepository
from upv_mcp.tools import deadlines as deadlines_tool
from upv_mcp.tools import next_class as next_class_tool
from upv_mcp.tools import schedule as schedule_tool

INSTRUCTIONS = """\
Calendario academico de un estudiante de la UPV (Universitat Politecnica de \
Valencia). Todas las horas son locales de Valencia (Europe/Madrid).

Elige la tool por el tipo de pregunta: get_next_class para lo inmediato sin fecha, \
get_schedule cuando haya un dia o un rango, y list_upcoming_deadlines para fechas \
limite. Revisa siempre meta.coverage_note antes de afirmar que algo no existe: esta \
version no ve los examenes ni las entregas de PoliformaT.\
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
    settings = load_settings()
    settings.ensure_dirs()
    cache = CacheRepository(settings.db_path, settings.timezone)
    try:
        yield AppContext(settings=settings, repo=AcademicRepository(settings, cache))
    finally:
        cache.close()


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
) -> ScheduleResult:
    app = ctx.request_context.lifespan_context
    return await schedule_tool.get_schedule(
        app.repo, start_date, end_date, limit=app.settings.max_results
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
) -> DeadlinesResult:
    app = ctx.request_context.lifespan_context
    return await deadlines_tool.list_upcoming_deadlines(
        app.repo, days_ahead, limit=app.settings.max_results
    )


def main() -> None:
    """Arranca el servidor por stdio (transporte por defecto en el SDK)."""
    mcp.run()


if __name__ == "__main__":  # pragma: no cover
    main()
