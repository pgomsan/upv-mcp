"""Tests del servidor MCP.

Aqui si se importa `mcp`: es la capa que lo conecta. Verifica que las tools estan
registradas, que sus descripciones cumplen las reglas de desambiguacion, y que el
esquema que ve el cliente es el correcto.
"""

from __future__ import annotations

import pytest

from upv_mcp.server import mcp
from upv_mcp.tools import deadlines as deadlines_tool
from upv_mcp.tools import next_class as next_class_tool
from upv_mcp.tools import schedule as schedule_tool

TOOL_NAMES = {"get_schedule", "get_next_class", "list_upcoming_deadlines"}


async def test_las_tres_tools_estan_registradas() -> None:
    tools = await mcp.list_tools()
    assert {t.name for t in tools} == TOOL_NAMES


async def test_el_esquema_de_entrada_es_el_esperado() -> None:
    tools = {t.name: t for t in await mcp.list_tools()}

    schedule = tools["get_schedule"].input_schema
    assert set(schedule["required"]) == {"start_date", "end_date"}
    assert schedule["properties"]["start_date"]["format"] == "date"

    # get_next_class no debe aceptar fechas: es siempre "desde ahora".
    assert not tools["get_next_class"].input_schema.get("required")
    assert "date" not in str(tools["get_next_class"].input_schema)

    deadlines = tools["list_upcoming_deadlines"].input_schema
    assert "days_ahead" in deadlines["properties"]
    assert not deadlines.get("required")


async def test_las_tools_declaran_salida_estructurada() -> None:
    """Los modelos pydantic deben viajar como structured output, no como texto."""
    for tool in await mcp.list_tools():
        assert tool.output_schema is not None, tool.name


@pytest.mark.parametrize(
    ("description", "otras"),
    [
        (schedule_tool.DESCRIPTION, ["get_next_class", "list_upcoming_deadlines"]),
        (next_class_tool.DESCRIPTION, ["get_schedule", "list_upcoming_deadlines"]),
        (deadlines_tool.DESCRIPTION, ["get_schedule", "get_next_class"]),
    ],
)
def test_cada_descripcion_desambigua_frente_a_las_otras(
    description: str, otras: list[str]
) -> None:
    """La descripcion es lo que decide si el modelo elige bien.

    Cada una debe decir explicitamente cuando NO usarla y redirigir por nombre a
    las otras dos, que es donde se producen las confusiones.
    """
    assert "NO LA USES" in description
    for otra in otras:
        assert otra in description, f"la descripcion no redirige a {otra}"


def test_la_tool_de_deadlines_declara_su_limitacion() -> None:
    """Es la unica que hoy puede devolver vacio por falta de datos, no por no haberlos."""
    texto = deadlines_tool.DESCRIPTION
    assert "NO PUEDES VER" in texto
    assert "PoliformaT" in texto
    assert "coverage_note" in texto


async def test_las_descripciones_son_compactas() -> None:
    """Descripciones enormes gastan contexto en cada tools/list."""
    for tool in await mcp.list_tools():
        assert tool.description is not None
        assert len(tool.description) < 2000, f"{tool.name} demasiado larga"
