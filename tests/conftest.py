"""Fixtures compartidas.

Los .ics de `tests/fixtures/` son anonimizados pero conservan la estructura exacta
del generador de la UPV (ASIC iCal Generator 1.0), que es lo que el parser cubre.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from upv_mcp.cache.db import CacheRepository
from upv_mcp.config import Settings

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def horario_ics() -> Path:
    """Horario real anonimizado: 8 eventos con los casos del generador de la UPV."""
    return FIXTURES / "horario_upv.ics"


@pytest.fixture
def casos_limite_ics() -> Path:
    """Aula vacia, aula sin edificio, sin docente, asignatura sin codigo."""
    return FIXTURES / "casos_limite.ics"


@pytest.fixture
def examenes_ics() -> Path:
    """Calendario de examenes (formato previsto para la v0.1)."""
    return FIXTURES / "examenes.ics"


@pytest.fixture
def settings(tmp_path: Path, horario_ics: Path) -> Settings:
    return Settings(
        schedule_ics_file=horario_ics,
        data_dir=tmp_path / "data",
        max_results=50,
    )


@pytest.fixture
def cache(tmp_path: Path) -> Iterator[CacheRepository]:
    with CacheRepository(tmp_path / "cache.db") as repo:
        yield repo


def pytest_addoption(parser: pytest.Parser) -> None:
    # Regenerar un golden tiene que ser una decision explicita, nunca un efecto de
    # que el test falle: si no, un cambio accidental se "arregla" solo.
    parser.addoption(
        "--update-golden",
        action="store_true",
        default=False,
        help="Reescribe los golden files de tests/data/ en vez de compararlos.",
    )
