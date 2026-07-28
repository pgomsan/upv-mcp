"""Tests del servidor MCP.

Aqui si se importa `mcp`: es la capa que lo conecta. Verifica que las tools estan
registradas, que sus descripciones cumplen las reglas de desambiguacion, y que el
esquema que ve el cliente es el correcto.
"""

from __future__ import annotations

import pytest

from upv_mcp.server import mcp
from upv_mcp.tools import announcements as announcements_tool
from upv_mcp.tools import deadlines as deadlines_tool
from upv_mcp.tools import materials as materials_tool
from upv_mcp.tools import next_class as next_class_tool
from upv_mcp.tools import schedule as schedule_tool

TOOL_NAMES = {
    "get_schedule",
    "get_next_class",
    "list_upcoming_deadlines",
    "list_announcements",
    "read_material",
}


async def test_las_tools_estan_registradas() -> None:
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
    assert "days_back" in deadlines["properties"]
    assert not deadlines.get("required")

    assert "days_back" in tools["list_announcements"].input_schema["properties"]


async def test_mirar_atras_es_opcional_y_apagado_por_defecto() -> None:
    """Lo normal es preguntar por el curso en marcha, no por lo ya entregado."""
    tools = {t.name: t for t in await mcp.list_tools()}
    days_back = tools["list_upcoming_deadlines"].input_schema["properties"]["days_back"]

    assert days_back.get("default") == 0


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
        (announcements_tool.DESCRIPTION, ["get_schedule", "list_upcoming_deadlines"]),
        (materials_tool.READ_DESCRIPTION, ["list_upcoming_deadlines"]),
    ],
)
def test_cada_descripcion_desambigua_frente_a_las_otras(description: str, otras: list[str]) -> None:
    """La descripcion es lo que decide si el modelo elige bien.

    Cada una debe decir explicitamente cuando NO usarla y redirigir por nombre a
    las tools con las que se puede confundir.
    """
    assert "NO LA USES" in description
    for otra in otras:
        assert otra in description, f"la descripcion no redirige a {otra}"


def test_la_tool_de_deadlines_no_promete_examenes() -> None:
    """Los examenes no los cubre ninguna fuente, y no se adivinan por titulo.

    En PoliformaT hay tareas tituladas "Examen" que no lo son, y examenes que no
    son tarea. La descripcion debe impedir que el modelo deduzca lo que no sabe.
    """
    texto = deadlines_tool.DESCRIPTION
    assert "no puedes verlos" in texto
    assert "NO deduzcas que no tiene ninguno" in texto
    assert "coverage_note" in texto


def test_la_tool_de_deadlines_distingue_desconocido_de_no_entregado() -> None:
    """ "No lo se" y "te falta entregarlo" son cosas muy distintas para un estudiante."""
    texto = deadlines_tool.DESCRIPTION
    assert "NO es lo mismo que no entregada" in texto
    assert "nunca que le falta entregarla" in texto


def test_los_materiales_no_son_una_tool() -> None:
    """Son contenido navegable: van como resource, y la tool vecina lo dice."""
    assert "upv://materiales" in announcements_tool.DESCRIPTION
    assert materials_tool.INDEX_URI == "upv://materiales"


async def test_las_descripciones_son_compactas() -> None:
    """Descripciones enormes gastan contexto en cada tools/list."""
    for tool in await mcp.list_tools():
        assert tool.description is not None
        assert len(tool.description) < 2000, f"{tool.name} demasiado larga"


async def test_los_resources_de_materiales_estan_registrados() -> None:
    plantillas = {t.uri_template for t in await mcp.list_resource_templates()}
    estaticos = {str(r.uri) for r in await mcp.list_resources()}

    assert materials_tool.COURSE_URI_TEMPLATE in plantillas
    assert materials_tool.INDEX_URI in estaticos
