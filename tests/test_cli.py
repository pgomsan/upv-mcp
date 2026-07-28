"""Tests del CLI de configuracion.

El caso sin terminal interactiva (`claude !`, scripts, CI) reventaba con un
traceback de getpass. No estaba cubierto, y por eso llego hasta el usuario.
"""

from __future__ import annotations

import io

import pytest

from upv_mcp import cli
from upv_mcp.config import SCHEDULE_KEY


@pytest.fixture
def llavero_falso(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Llavero en memoria: los tests no deben tocar el Keychain real."""
    store: dict[str, str] = {}
    monkeypatch.setattr(cli, "write_secret", lambda k, v: store.__setitem__(k, v))
    monkeypatch.setattr(cli, "read_secret", store.get)
    monkeypatch.setattr(cli, "delete_secret", lambda k: store.pop(k, None))
    return store


def test_set_lee_de_stdin_sin_terminal(
    monkeypatch: pytest.MonkeyPatch, llavero_falso: dict[str, str]
) -> None:
    """Sin TTY se lee de stdin en vez de reventar con EOFError."""
    monkeypatch.setattr("sys.stdin", io.StringIO("https://www.upv.es/ical/TOKEN\n"))

    assert cli.main(["set", "schedule"]) == 0
    assert llavero_falso[SCHEDULE_KEY] == "https://www.upv.es/ical/TOKEN"


def test_set_sin_tty_y_sin_stdin_falla_con_mensaje_util(
    monkeypatch: pytest.MonkeyPatch,
    llavero_falso: dict[str, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(""))

    assert cli.main(["set", "schedule"]) == 1
    error = capsys.readouterr().err
    assert "terminal interactiva" in error
    assert "stdin" in error
    assert not llavero_falso


def test_set_usa_getpass_si_hay_terminal(
    monkeypatch: pytest.MonkeyPatch, llavero_falso: dict[str, str]
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(cli, "getpass", lambda prompt: "https://www.upv.es/ical/X  ")

    assert cli.main(["set", "schedule"]) == 0
    assert llavero_falso[SCHEDULE_KEY] == "https://www.upv.es/ical/X"


def test_set_rechaza_lo_que_no_es_url(
    monkeypatch: pytest.MonkeyPatch,
    llavero_falso: dict[str, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("mi horario\n"))

    assert cli.main(["set", "schedule"]) == 1
    assert "no parece una URL" in capsys.readouterr().err
    assert not llavero_falso


def test_show_enmascara_el_token(
    llavero_falso: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    """El token no debe aparecer entero en pantalla ni en un screenshot."""
    token = "A" * 96
    llavero_falso[SCHEDULE_KEY] = f"https://www.upv.es/ical/{token}"

    assert cli.main(["show", "schedule"]) == 0
    salida = capsys.readouterr().out
    assert token not in salida
    assert "..." in salida


def test_show_sin_configurar(
    llavero_falso: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["show"]) == 0
    salida = capsys.readouterr().out
    assert "sin configurar" in salida
    assert "schedule" in salida
    assert "exams" in salida


def test_delete(llavero_falso: dict[str, str]) -> None:
    llavero_falso[SCHEDULE_KEY] = "https://www.upv.es/ical/X"

    assert cli.main(["delete", "schedule"]) == 0
    assert SCHEDULE_KEY not in llavero_falso
