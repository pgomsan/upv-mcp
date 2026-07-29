"""Tests de configuracion: rutas XDG, origenes y resolucion de secretos."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from upv_mcp import config as config_module
from upv_mcp.config import APP_NAME, SCHEDULE_KEY, Settings, load_settings


def test_requires_a_source() -> None:
    """Sin ningun calendario el servidor no debe llegar a arrancar."""
    with pytest.raises(ValidationError, match="Falta el origen de datos"):
        Settings()


def test_data_dir_follows_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    settings = Settings(schedule_ics_url="https://example.org/cal.ics")
    assert settings.data_dir == tmp_path / APP_NAME
    assert settings.db_path == tmp_path / APP_NAME / "cache.db"


def test_local_file_is_a_valid_source(tmp_path: Path) -> None:
    settings = Settings(schedule_ics_file=tmp_path / "horario.ics")
    assert settings.schedule_ics_url is None
    assert settings.max_results == 50
    assert [c.name for c in settings.calendars] == ["schedule"]


def test_sin_calendario_de_examenes_se_declara(tmp_path: Path) -> None:
    """La v0 tipica solo tiene horario: las tools deben poder avisarlo."""
    settings = Settings(schedule_ics_file=tmp_path / "horario.ics")
    assert settings.has_exam_calendar is False

    con_examenes = Settings(
        schedule_ics_file=tmp_path / "horario.ics",
        exams_ics_file=tmp_path / "examenes.ics",
    )
    assert con_examenes.has_exam_calendar is True
    assert [c.name for c in con_examenes.calendars] == ["schedule", "exams"]


def test_origin_no_revela_la_url(tmp_path: Path) -> None:
    """El token de la URL no debe acabar en un log ni en un mensaje de error."""
    settings = Settings(schedule_ics_url="https://www.upv.es/ical/TOKEN_SECRETO")
    assert "TOKEN_SECRETO" not in settings.calendars[0].origin


def test_el_entorno_gana_al_llavero(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un test o un CI deben poder forzar el origen sin tocar el Keychain."""
    monkeypatch.setenv("UPV_MCP_SCHEDULE_ICS_URL", "https://example.org/desde-entorno.ics")
    monkeypatch.setattr(config_module, "read_secret", lambda key: "https://del-llavero.ics")

    settings = load_settings()
    assert settings.schedule_ics_url == "https://example.org/desde-entorno.ics"


def test_usa_el_llavero_si_no_hay_entorno(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UPV_MCP_SCHEDULE_ICS_URL", raising=False)
    monkeypatch.delenv("UPV_MCP_EXAMS_ICS_URL", raising=False)
    monkeypatch.setattr(
        config_module,
        "read_secret",
        lambda key: "https://del-llavero.ics" if key == SCHEDULE_KEY else None,
    )

    settings = load_settings()
    assert settings.schedule_ics_url == "https://del-llavero.ics"
    assert settings.exams_ics_url is None


def test_llavero_ausente_no_rompe(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Sin backend de llavero (CI, contenedor) se cae al fallback de entorno."""
    monkeypatch.setattr(config_module, "read_secret", lambda key: None)
    monkeypatch.setenv("UPV_MCP_SCHEDULE_ICS_FILE", str(tmp_path / "h.ics"))

    settings = load_settings()
    assert settings.schedule_ics_file == tmp_path / "h.ics"


def test_normaliza_webcal_a_https() -> None:
    """La intranet de la UPV entrega el enlace de examenes como webcal://.

    Es https:// con otro nombre, pero ningun cliente HTTP lo entiende: sin
    normalizar, la descarga fallaba con "unsupported protocol" y ademas se
    reintentaba tres veces un error que no se arregla solo.
    """
    settings = Settings(exams_ics_url="webcal://www.upv.es/ical/TOKEN")

    assert settings.calendars[0].url == "https://www.upv.es/ical/TOKEN"


def test_normaliza_webcals_y_respeta_https() -> None:
    assert (
        Settings(schedule_ics_url="WEBCALS://x.upv.es/a.ics").calendars[0].url
        == "https://x.upv.es/a.ics"
    )
    assert (
        Settings(schedule_ics_url="https://x.upv.es/a.ics").calendars[0].url
        == "https://x.upv.es/a.ics"
    )
