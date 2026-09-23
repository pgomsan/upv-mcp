"""Los invariantes de capas, como test.

Son la razon de ser de la estructura del repo: permiten anadir PoliformaT en la v1
tocando solo `sources/`. Comprobarlos a mano no sirve, porque se rompen justo cuando
uno tiene prisa. Se analizan los imports reales con `ast`, no con grep: una mencion
en un docstring no es una violacion.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src" / "upv_mcp"


def _imported_modules(path: Path) -> set[str]:
    """Modulos raiz importados por un fichero, segun su AST."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module.split(".")[0])
    return modules


def _python_files(*parts: str) -> list[Path]:
    return sorted((SRC.joinpath(*parts)).rglob("*.py"))


@pytest.mark.parametrize("path", _python_files("sources"), ids=lambda p: p.name)
def test_sources_no_conoce_mcp(path: Path) -> None:
    """Una fuente sabe de red y de parseo, no de protocolo."""
    assert "mcp" not in _imported_modules(path)


@pytest.mark.parametrize("path", _python_files("cache"), ids=lambda p: p.name)
def test_cache_no_conoce_mcp(path: Path) -> None:
    assert "mcp" not in _imported_modules(path)


@pytest.mark.parametrize("path", _python_files("tools"), ids=lambda p: p.name)
def test_tools_no_conoce_mcp_ni_red_ni_parseo(path: Path) -> None:
    """Si una tool necesita httpx2 o icalendar, el trabajo va en sources/."""
    prohibidos = {"mcp", "httpx2", "httpx", "icalendar"} & _imported_modules(path)
    assert not prohibidos, f"{path.name} importa {prohibidos}"


def test_solo_server_importa_el_sdk() -> None:
    """Un unico punto de contacto con el SDK: es lo que hace barata su migracion."""
    con_mcp = {p.name for p in SRC.rglob("*.py") if "mcp" in _imported_modules(p)}
    assert con_mcp == {"server.py"}


def test_repository_no_conoce_mcp() -> None:
    assert "mcp" not in _imported_modules(SRC / "repository.py")


def test_export_ics_es_pura() -> None:
    """El generador del .ics recibe modelos y devuelve texto: ni red, ni cache, ni MCP."""
    tree = ast.parse((SRC / "export_ics.py").read_text(encoding="utf-8"))
    importados: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            importados.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            importados.add(node.module)
    prohibidos = {
        m
        for m in importados
        if m.split(".")[0] in {"mcp", "httpx2", "httpx", "sqlite3", "icalendar", "pathlib"}
        or m.startswith(("upv_mcp.server", "upv_mcp.cache", "upv_mcp.repository"))
        or m.startswith("upv_mcp.sources")
    }
    assert not prohibidos, f"export_ics.py importa {prohibidos}"
