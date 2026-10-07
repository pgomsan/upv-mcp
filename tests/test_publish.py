"""`upv-publish`: generacion, deduplicado por hash, subida con reintentos y token.

Nada toca red real, el Llavero real ni el build/ del repo: todo se inyecta.
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx2
import pytest

from upv_mcp import publish
from upv_mcp.cache.db import CacheRepository
from upv_mcp.config import Settings
from upv_mcp.export_ics import build_calendar
from upv_mcp.ics_state import SqliteEventStateStore
from upv_mcp.models import Assignment, Course, EventKind, SourceName, Submission, SubmissionStatus
from upv_mcp.publish import PublishError, huella, leer_token, publicar, subir
from upv_mcp.repository import AcademicRepository

MADRID = ZoneInfo("Europe/Madrid")
NOW = datetime(2026, 9, 22, 9, 0, tzinfo=UTC)
URL = "https://feed.example.workers.dev/entregas.ics"
TOKEN = "tok-9f2Qx7LmZ4rT1vB8nK3wY6hD0sPjU5cE"


def _entrega(
    clave: str,
    *,
    status: SubmissionStatus = SubmissionStatus.NOT_SUBMITTED,
    graded: bool = False,
    kind: EventKind = EventKind.ASSIGNMENT,
    due: datetime | None = None,
) -> Assignment:
    return Assignment(
        uid=f"poliformat:assignment:{clave}",
        kind=kind,
        title=f"Tarea {clave}",
        course=Course(code="14534", name="Infraestructura", acronym="ICD"),
        due=due or datetime(2026, 10, 19, 19, 9, tzinfo=MADRID),
        url=f"https://poliformat.upv.es/direct/assignment/{clave}",
        source=SourceName.POLIFORMAT,
        submission=Submission(status=status, graded=graded),
    )


DEADLINES = [
    _entrega("a"),
    _entrega("b", status=SubmissionStatus.UNKNOWN),
    _entrega("c", status=SubmissionStatus.SUBMITTED),
    _entrega("d", graded=True),
    _entrega("e", kind=EventKind.EXAM),
]


@dataclass
class Servidor:
    """Worker falso: responde con la secuencia de `respuestas` y apunta lo recibido."""

    respuestas: list[int | type[httpx2.TransportError]]
    peticiones: list[httpx2.Request] = field(default_factory=list)

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.peticiones.append(request)
        siguiente = self.respuestas.pop(0) if len(self.respuestas) > 1 else self.respuestas[0]
        if isinstance(siguiente, int):
            return httpx2.Response(siguiente)
        raise siguiente("conexion rechazada", request=request)

    def cliente(self) -> httpx2.Client:
        return httpx2.Client(transport=httpx2.MockTransport(self))


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SqliteEventStateStore]:
    with SqliteEventStateStore(tmp_path / "cache.db") as s:
        yield s


@pytest.fixture(autouse=True)
def _feed_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(publish.FEED_URL_ENV, URL)


def _publicar(
    store: SqliteEventStateStore,
    servidor: Servidor,
    tmp_path: Path,
    *,
    deadlines: list[Assignment] = DEADLINES,
    now: datetime = NOW,
    dry_run: bool = False,
    stdout: Callable[[bytes], object] | None = None,
) -> list[float]:
    esperas: list[float] = []
    publicar(
        deadlines,
        store,
        now=now,
        dry_run=dry_run,
        obtener_token=lambda: TOKEN,
        cliente=servidor.cliente(),
        dormir=esperas.append,
        salida=tmp_path / "build" / "entregas.ics",
        stdout=stdout or (lambda _b: None),
    )
    return esperas


# -- Salida y hash --------------------------------------------------------------------


def test_la_salida_esta_en_build_y_nunca_en_la_raiz() -> None:
    raiz = Path(__file__).resolve().parents[1]
    assert raiz / "build" / "entregas.ics" == publish.SALIDA
    assert publish.SALIDA.parent != raiz
    assert publish.SALIDA.name != "horario.ics"


def test_huella_ignora_dtstamp_pero_no_el_contenido() -> None:
    uno = build_calendar(DEADLINES, now=NOW)
    otro = build_calendar(DEADLINES, now=NOW + timedelta(hours=1))
    assert uno != otro
    assert huella(uno) == huella(uno.replace("DTSTAMP:20260922T090000Z", "DTSTAMP:X"))
    assert huella(uno) != huella(uno.replace("Tarea a", "Tarea A"))


def test_crea_build_y_escribe_el_ics(store: SqliteEventStateStore, tmp_path: Path) -> None:
    _publicar(store, Servidor([204]), tmp_path)
    escrito = (tmp_path / "build" / "entregas.ics").read_bytes()
    assert escrito.startswith(b"BEGIN:VCALENDAR\r\n")
    assert escrito.count(b"BEGIN:VEVENT") == 2  # a y b; c, d y e excluidos


# -- Subir o no subir -----------------------------------------------------------------


def test_primera_pasada_sube_con_token_y_content_type(
    store: SqliteEventStateStore, tmp_path: Path
) -> None:
    servidor = Servidor([204])
    _publicar(store, servidor, tmp_path)

    (peticion,) = servidor.peticiones
    assert peticion.method == "PUT"
    assert str(peticion.url) == URL
    assert peticion.headers["Authorization"] == f"Bearer {TOKEN}"
    assert peticion.headers["Content-Type"] == "text/calendar; charset=utf-8"
    assert peticion.content == (tmp_path / "build" / "entregas.ics").read_bytes()
    assert store.last_uploaded_sha256("entregas") is not None


def test_sin_cambios_no_sube(store: SqliteEventStateStore, tmp_path: Path) -> None:
    _publicar(store, Servidor([204]), tmp_path)

    segunda = Servidor([204])
    _publicar(store, segunda, tmp_path, now=NOW + timedelta(hours=1))
    assert segunda.peticiones == []


def test_con_cambios_vuelve_a_subir(store: SqliteEventStateStore, tmp_path: Path) -> None:
    _publicar(store, Servidor([204]), tmp_path)

    segunda = Servidor([204])
    otra = [*DEADLINES, _entrega("nueva")]
    _publicar(store, segunda, tmp_path, deadlines=otra, now=NOW + timedelta(hours=1))
    assert len(segunda.peticiones) == 1


def test_si_la_subida_falla_la_siguiente_pasada_reintenta(
    store: SqliteEventStateStore, tmp_path: Path
) -> None:
    with pytest.raises(PublishError):
        _publicar(store, Servidor([500]), tmp_path)
    assert store.last_uploaded_sha256("entregas") is None

    segunda = Servidor([204])
    _publicar(store, segunda, tmp_path, now=NOW + timedelta(hours=1))
    assert len(segunda.peticiones) == 1


def test_dry_run_no_escribe_ni_sube(store: SqliteEventStateStore, tmp_path: Path) -> None:
    servidor = Servidor([204])
    impreso: list[bytes] = []
    _publicar(store, servidor, tmp_path, dry_run=True, stdout=impreso.append)

    assert servidor.peticiones == []
    assert not (tmp_path / "build").exists()
    assert store.all() == []
    assert store.last_uploaded_sha256("entregas") is None
    assert impreso == [build_calendar(DEADLINES, now=NOW).encode("utf-8")]


def test_sin_feed_url_falla_sin_subir(
    store: SqliteEventStateStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(publish.FEED_URL_ENV)
    servidor = Servidor([204])
    with pytest.raises(PublishError, match="UPV_FEED_URL"):
        _publicar(store, servidor, tmp_path)
    assert servidor.peticiones == []


# -- Reintentos -----------------------------------------------------------------------


def _subir(servidor: Servidor) -> tuple[int, list[float]]:
    esperas: list[float] = []
    intentos = subir(URL, b"x", TOKEN, cliente=servidor.cliente(), dormir=esperas.append)
    return intentos, esperas


def test_reintenta_5xx_con_backoff_exponencial() -> None:
    servidor = Servidor([502, 503, 204])
    assert _subir(servidor) == (3, [1.0, 2.0])


def test_reintenta_errores_de_red() -> None:
    servidor = Servidor([httpx2.ConnectError, 204])
    assert _subir(servidor) == (2, [1.0])


def test_tres_fallos_y_se_rinde() -> None:
    servidor = Servidor([500])
    with pytest.raises(PublishError, match="3 intentos"):
        _subir(servidor)
    assert len(servidor.peticiones) == 3


@pytest.mark.parametrize("codigo", [401, 403])
def test_401_y_403_no_se_reintentan(codigo: int) -> None:
    servidor = Servidor([codigo])
    with pytest.raises(PublishError, match=str(codigo)):
        _subir(servidor)
    assert len(servidor.peticiones) == 1


def test_otro_4xx_tampoco_se_reintenta() -> None:
    servidor = Servidor([413])
    with pytest.raises(PublishError, match="413"):
        _subir(servidor)
    assert len(servidor.peticiones) == 1


# -- Token ----------------------------------------------------------------------------


def _security(stdout: str, returncode: int) -> Callable[..., subprocess.CompletedProcess[str]]:
    def ejecutar(comando: list[str], **_: Any) -> subprocess.CompletedProcess[str]:  # noqa: ANN401
        assert comando == [
            "security",
            "find-generic-password",
            "-a",
            "upv-mcp",
            "-s",
            "upv-feed-token",
            "-w",
        ]
        return subprocess.CompletedProcess(comando, returncode, stdout=stdout, stderr="")

    return ejecutar


def test_leer_token_del_llavero() -> None:
    assert leer_token(_security(TOKEN + "\n", 0)) == TOKEN


def test_sin_token_explica_como_darlo_de_alta() -> None:
    with pytest.raises(PublishError) as exc:
        leer_token(_security("", 44))
    mensaje = str(exc.value)
    assert "security add-generic-password -a upv-mcp -s upv-feed-token -w" in mensaje
    assert "44" in mensaje


def test_sin_binario_security_tambien_lo_explica() -> None:
    def falla(*_: Any, **__: Any) -> subprocess.CompletedProcess[str]:  # noqa: ANN401
        raise FileNotFoundError("security")

    with pytest.raises(PublishError, match="add-generic-password"):
        leer_token(falla)


# -- main de punta a punta: el token no sale nunca ------------------------------------


def _trozos(secreto: str, largo: int = 6) -> set[str]:
    return {secreto[i : i + largo] for i in range(len(secreto) - largo + 1)}


@pytest.mark.parametrize(
    "respuestas",
    [[204], [401], [403], [500], [httpx2.ConnectError], [httpx2.ReadTimeout]],
    ids=["ok", "401", "403", "500x3", "red", "timeout"],
)
def test_el_token_nunca_aparece_en_la_salida(
    respuestas: list[int | type[httpx2.TransportError]],
    settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    servidor = Servidor(respuestas)

    async def deadlines(_repo: AcademicRepository, _days: int) -> list[Assignment]:
        return DEADLINES

    monkeypatch.setattr(publish, "load_settings", lambda: settings)
    monkeypatch.setattr(publish, "obtener_deadlines", deadlines)
    monkeypatch.setattr(publish, "SALIDA", tmp_path / "build" / "entregas.ics")
    monkeypatch.setattr(publish, "_nuevo_cliente", servidor.cliente)
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    monkeypatch.setattr(subprocess, "run", _security(TOKEN, 0))

    codigo = publish.main([])

    assert servidor.peticiones, "el test no llego a subir: no prueba nada"
    assert servidor.peticiones[0].headers["Authorization"] == f"Bearer {TOKEN}"
    assert codigo == (0 if respuestas == [204] else 1)

    capturado = capsys.readouterr()
    todo = capturado.out + capturado.err + caplog.text
    assert "upv-publish:" in todo
    filtrados = [t for t in _trozos(TOKEN) if t in todo]
    assert not filtrados, f"fragmentos del token en la salida: {filtrados}"


def test_sin_token_main_falla_y_lo_explica(
    settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def deadlines(_repo: AcademicRepository, _days: int) -> list[Assignment]:
        return DEADLINES

    monkeypatch.setattr(publish, "load_settings", lambda: settings)
    monkeypatch.setattr(publish, "obtener_deadlines", deadlines)
    monkeypatch.setattr(publish, "SALIDA", tmp_path / "build" / "entregas.ics")
    monkeypatch.setattr(subprocess, "run", _security("", 44))

    assert publish.main([]) == 1
    salida = capsys.readouterr().out
    assert "add-generic-password" in salida
    assert "FALLO" in salida


def test_el_log_lleva_timestamp_iso_y_el_resumen(
    settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    servidor = Servidor([204])

    async def deadlines(_repo: AcademicRepository, _days: int) -> list[Assignment]:
        return DEADLINES

    monkeypatch.setattr(publish, "load_settings", lambda: settings)
    monkeypatch.setattr(publish, "obtener_deadlines", deadlines)
    monkeypatch.setattr(publish, "SALIDA", tmp_path / "build" / "entregas.ics")
    monkeypatch.setattr(publish, "_nuevo_cliente", servidor.cliente)
    monkeypatch.setattr(subprocess, "run", _security(TOKEN, 0))

    assert publish.main([]) == 0
    lineas = capsys.readouterr().out.splitlines()
    for linea in lineas:
        datetime.fromisoformat(linea.split(" ", 1)[0])  # cada linea empieza con ISO 8601
    texto = "\n".join(lineas)
    assert "5 fechas limite en el horizonte: 2 eventos" in texto
    assert "3 excluidos (1 corregida, 1 entregada, 1 examen)" in texto
    assert "sha256=" in texto
    assert "Subido a feed.example.workers.dev en 1 intento(s)." in texto
    assert "upv-publish: fin en" in texto


def test_dry_run_deja_stdout_solo_para_el_ics(
    settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfdbinary: pytest.CaptureFixture[bytes],
) -> None:
    async def deadlines(_repo: AcademicRepository, _days: int) -> list[Assignment]:
        return DEADLINES

    def sin_red() -> httpx2.Client:
        raise AssertionError("--dry-run no debe crear cliente HTTP")

    def sin_llavero(*_: Any, **__: Any) -> subprocess.CompletedProcess[str]:  # noqa: ANN401
        raise AssertionError("--dry-run no debe leer el token")

    monkeypatch.setattr(publish, "load_settings", lambda: settings)
    monkeypatch.setattr(publish, "obtener_deadlines", deadlines)
    monkeypatch.setattr(publish, "SALIDA", tmp_path / "build" / "entregas.ics")
    monkeypatch.setattr(publish, "_nuevo_cliente", sin_red)
    monkeypatch.setattr(subprocess, "run", sin_llavero)

    assert publish.main(["--dry-run"]) == 0
    capturado = capfdbinary.readouterr()
    assert capturado.out.startswith(b"BEGIN:VCALENDAR\r\n")
    assert capturado.out.endswith(b"END:VCALENDAR\r\n")
    assert b"upv-publish:" in capturado.err
    assert not (tmp_path / "build").exists()


# -- Guardas sobre los datos ----------------------------------------------------------


async def test_sin_poliformat_no_se_publica(settings: Settings) -> None:
    """Un feed vacio borraria todos los eventos del movil: se aborta antes."""
    with CacheRepository(settings.db_path) as cache:
        repo = AcademicRepository(settings, cache)
        assert not repo.poliformat_configured
        with pytest.raises(PublishError, match="no esta configurado"):
            await publish.obtener_deadlines(repo, 90)


async def test_con_el_login_bloqueado_no_se_publica_ni_se_toca_cas(settings: Settings) -> None:
    settings.cas_lock_path.parent.mkdir(parents=True, exist_ok=True)
    settings.cas_lock_path.write_text("rechazado", encoding="utf-8")

    class _NoDebeLlamarse:
        name = "poliformat"

        async def fetch(self, *, force_refresh: bool = False) -> None:
            raise AssertionError("con el bloqueo puesto no se hace login")

    with CacheRepository(settings.db_path) as cache:
        repo = AcademicRepository(settings, cache, poliformat=_NoDebeLlamarse())  # type: ignore[arg-type]
        with pytest.raises(PublishError, match="BLOQUEADO"):
            await publish.obtener_deadlines(repo, 90)


def test_el_publicador_consulta_poliformat_en_cada_pasada_horaria(settings: Settings) -> None:
    """Regresion: con la cache de 6 h, una entrega nueva tardaba hasta 6 h en salir."""
    propia = publish.settings_publicador(settings)
    assert propia.cache_ttl_seconds < 60 * 60, "el agente corre cada hora"
    assert propia.db_path == settings.db_path
    assert settings.cache_ttl_seconds == 6 * 60 * 60, "el servidor MCP no cambia"
