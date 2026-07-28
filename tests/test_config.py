"""Tests de configuracion: rutas XDG y validacion del origen de datos."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from upv_mcp.config import APP_NAME, Settings


def test_requires_a_source() -> None:
    """Sin ics_url ni ics_file el servidor no debe llegar a arrancar."""
    with pytest.raises(ValidationError, match="Falta el origen de datos"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_data_dir_follows_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    settings = Settings(ics_url="https://example.org/cal.ics", _env_file=None)  # type: ignore[call-arg]
    assert settings.data_dir == tmp_path / APP_NAME
    assert settings.db_path == tmp_path / APP_NAME / "cache.db"


def test_local_file_is_a_valid_source(tmp_path: Path) -> None:
    settings = Settings(ics_file=tmp_path / "horario.ics", _env_file=None)  # type: ignore[call-arg]
    assert settings.ics_url is None
    assert settings.max_results == 50
