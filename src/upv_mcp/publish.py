"""`upv-publish`: genera el .ics de entregas y lo sube al Worker de Cloudflare.

Pensado para lanzarse desde launchd y leerse semanas despues en su log: cada
pasada deja por escrito cuantos eventos salen, cuantos se excluyen y por que, el
hash, si se subio y cuanto tardo.

Flujo:
1. Las fechas limite salen por la MISMA ruta que la tool `list_upcoming_deadlines`
   (tools/deadlines.py sobre AcademicRepository): nada de scraping propio.
2. `build_calendar` con el estado SQLite (SEQUENCE persistente, ics_state.py).
3. Se escribe en build/entregas.ics. NUNCA en la raiz: horario.ics es ENTRADA.
4. Si el hash coincide con el de la ultima subida, no se sube nada.
5. Si no, PUT a $UPV_FEED_URL con el token del Llavero de macOS.

El hash ignora las lineas DTSTAMP: DTSTAMP es la hora de generacion y cambia en
cada pasada, asi que con el cuerpo tal cual nunca habria "sin cambios".

El token vive en el Llavero y solo en el: no se acepta por argumento, entorno ni
fichero, y no aparece nunca en un log ni en un mensaje de error.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import os
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

import httpx2

from upv_mcp.cache.db import CacheRepository
from upv_mcp.config import load_settings
from upv_mcp.export_ics import EventState, build_calendar, motivo_exclusion
from upv_mcp.ics_state import SqliteEventStateStore
from upv_mcp.models import Assignment
from upv_mcp.repository import AcademicRepository
from upv_mcp.sources.base import SourceError
from upv_mcp.tools import deadlines as deadlines_tool

log = logging.getLogger("upv_mcp.publish")

FEED = "entregas"
DEFAULT_DAYS = 90
#: Sin tope real: el feed tiene que llevar TODAS las entregas del horizonte. Si se
#: truncara, lo que quedase fuera se marcaria como retirado.
_SIN_LIMITE = 100_000

KEYCHAIN_ACCOUNT = "upv-mcp"
KEYCHAIN_SERVICE = "upv-feed-token"
FEED_URL_ENV = "UPV_FEED_URL"

INTENTOS = 3
BACKOFF_BASE_S = 1.0
TIMEOUT_S = 30.0

_RAIZ_REPO = Path(__file__).resolve().parents[2]
#: Unica salida. Fuera de la raiz del repo a proposito: alli vive horario.ics, que
#: es ENTRADA y no se toca jamas.
SALIDA = _RAIZ_REPO / "build" / f"{FEED}.ics"


class PublishError(Exception):
    """Fallo que aborta la publicacion. El mensaje va al log: nunca lleva el token."""


# -- Logging ------------------------------------------------------------------------


class _IsoFormatter(logging.Formatter):
    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:  # noqa: N802
        return datetime.fromtimestamp(record.created).astimezone().isoformat(timespec="seconds")


class _Censura(logging.Filter):
    """Red de seguridad: si el token llegara a un mensaje, se sustituye."""

    def __init__(self, secreto: str) -> None:
        super().__init__()
        self._secreto = secreto

    def filter(self, record: logging.LogRecord) -> bool:
        mensaje = record.getMessage()
        if self._secreto in mensaje:
            record.msg, record.args = mensaje.replace(self._secreto, "[CENSURADO]"), None
        return True


def _configurar_logging(*, dry_run: bool = False) -> None:
    # En --dry-run stdout es el .ics: el log va a stderr para no mezclarse.
    manejador = logging.StreamHandler(sys.stderr if dry_run else sys.stdout)
    manejador.setFormatter(_IsoFormatter("%(asctime)s %(levelname)s %(message)s"))
    log.handlers[:] = [manejador]
    log.setLevel(logging.INFO)
    log.propagate = False


# -- Datos --------------------------------------------------------------------------


async def obtener_deadlines(repo: AcademicRepository, days: int) -> list[Assignment]:
    """Las fechas limite del horizonte, por la misma ruta que la tool MCP.

    Aborta si PoliformaT no esta configurado, fallo o no se ha consultado nunca: un
    feed vacio borraria todos los eventos del movil y retiraria todo el estado.
    """
    resultado = await deadlines_tool.list_upcoming_deadlines(
        repo, days_ahead=days, limit=_SIN_LIMITE
    )
    if not repo.poliformat_configured:
        raise PublishError(
            "PoliformaT no esta configurado (`upv-mcp-config set poliformat`). "
            "No se publica: un feed vacio borraria los eventos del movil."
        )
    if repo.poliformat_failed:
        raise PublishError(
            "PoliformaT no respondio en este refresco. No se publica con datos de "
            "cache; se reintentara en la proxima pasada."
        )
    if repo.cache.fetched_at("poliformat") is None:
        raise PublishError("PoliformaT no se ha consultado nunca: no hay entregas que publicar.")
    if resultado.meta.truncated:
        raise PublishError(
            f"La lista de entregas vino truncada ({resultado.meta.returned} de "
            f"{resultado.meta.total_matching}). No se publica un feed incompleto."
        )
    return list(resultado.deadlines)


def huella(ics: str) -> str:
    """sha256 del feed sin las lineas DTSTAMP (cambian en cada pasada)."""
    lineas = (linea for linea in ics.split("\r\n") if not linea.startswith("DTSTAMP:"))
    return hashlib.sha256("\r\n".join(lineas).encode("utf-8")).hexdigest()


def escribir_salida(ics: str, destino: Path = SALIDA) -> None:
    """Escritura atomica: quien lea el fichero nunca ve uno a medias."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporal = destino.with_suffix(".ics.tmp")
    temporal.write_bytes(ics.encode("utf-8"))
    os.replace(temporal, destino)


class _EstadoSoloLectura:
    """Para --dry-run: parte del estado real y apunta los cambios solo en memoria."""

    def __init__(self, inicial: Sequence[EventState]) -> None:
        self._filas = {e.uid: e for e in inicial}

    def get(self, uid: str) -> EventState | None:
        return self._filas.get(uid)

    def put(self, state: EventState) -> None:
        self._filas[state.uid] = state

    def all(self) -> list[EventState]:
        return [self._filas[k] for k in sorted(self._filas)]

    def delete(self, uid: str) -> None:
        self._filas.pop(uid, None)


# -- Token y subida -----------------------------------------------------------------

_ALTA_TOKEN = (
    f"Dalo de alta en el Llavero con:\n"
    f"    security add-generic-password -a {KEYCHAIN_ACCOUNT} -s {KEYCHAIN_SERVICE} -w\n"
    "Sin valor detras de -w, te lo pide por teclado y no queda en el historial."
)


def leer_token(
    ejecutar: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> str:
    """Token del Llavero de macOS. Nunca de un .env, un argumento o un literal."""
    ejecutar = ejecutar or subprocess.run
    comando = [
        "security",
        "find-generic-password",
        "-a",
        KEYCHAIN_ACCOUNT,
        "-s",
        KEYCHAIN_SERVICE,
        "-w",
    ]
    try:
        salida = ejecutar(comando, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise PublishError(
            f"No se pudo ejecutar `security` ({type(exc).__name__}). {_ALTA_TOKEN}"
        ) from None
    token = (salida.stdout or "").strip()
    if salida.returncode != 0 or not token:
        raise PublishError(
            f"No hay token en el Llavero (servicio {KEYCHAIN_SERVICE}, cuenta "
            f"{KEYCHAIN_ACCOUNT}; `security` salio con codigo {salida.returncode}). "
            f"{_ALTA_TOKEN}"
        )
    return token


def subir(
    url: str,
    cuerpo: bytes,
    token: str,
    *,
    cliente: httpx2.Client,
    dormir: Callable[[float], None] | None = None,
) -> int:
    """PUT del feed. Devuelve el numero de intentos usados.

    Reintenta errores de red y 5xx con backoff exponencial. Un 401/403 aborta al
    momento: reintentar con un token malo no lo arregla. Los mensajes de error
    llevan el codigo HTTP o el tipo de excepcion, nunca cabeceras ni el cuerpo de
    la peticion.
    """
    dormir = dormir or time.sleep
    cabeceras = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "text/calendar; charset=utf-8",
    }
    ultimo_error = ""
    for intento in range(1, INTENTOS + 1):
        try:
            respuesta = cliente.put(url, content=cuerpo, headers=cabeceras)
        except httpx2.TransportError as exc:
            ultimo_error = f"error de red ({type(exc).__name__})"
        else:
            codigo = respuesta.status_code
            if 200 <= codigo < 300:
                return intento
            if codigo in (401, 403):
                raise PublishError(
                    f"El Worker rechazo el token (HTTP {codigo}). No se reintenta: "
                    f"revisa que el del Llavero ({KEYCHAIN_SERVICE}) coincida con el "
                    "secret FEED_TOKEN del Worker."
                )
            if codigo < 500:
                raise PublishError(f"El Worker rechazo la subida (HTTP {codigo}).")
            ultimo_error = f"HTTP {codigo}"

        if intento < INTENTOS:
            espera = BACKOFF_BASE_S * 2 ** (intento - 1)
            log.warning(
                "Intento %d/%d fallido: %s. Reintento en %.0f s.",
                intento,
                INTENTOS,
                ultimo_error,
                espera,
            )
            dormir(espera)
    raise PublishError(f"La subida fallo tras {INTENTOS} intentos; el ultimo: {ultimo_error}.")


# -- Orquestacion -------------------------------------------------------------------


def _resumen_exclusiones(deadlines: Sequence[Assignment]) -> str:
    motivos = Counter(m for a in deadlines if (m := motivo_exclusion(a)) is not None)
    if not motivos:
        return "0 excluidos"
    detalle = ", ".join(f"{n} {motivo}" for motivo, n in sorted(motivos.items()))
    return f"{sum(motivos.values())} excluidos ({detalle})"


def _nuevo_cliente() -> httpx2.Client:
    return httpx2.Client(timeout=TIMEOUT_S)


def _stdout_bytes(datos: bytes) -> None:
    # Bytes, no texto: ni el locale de launchd ni el modo texto tocan CRLF o UTF-8.
    sys.stdout.flush()
    sys.stdout.buffer.write(datos)
    sys.stdout.buffer.flush()


def publicar(
    deadlines: Sequence[Assignment],
    store: SqliteEventStateStore,
    *,
    now: datetime,
    dry_run: bool,
    obtener_token: Callable[[], str] | None = None,
    cliente: httpx2.Client | None = None,
    dormir: Callable[[float], None] | None = None,
    salida: Path | None = None,
    stdout: Callable[[bytes], object] | None = None,
) -> None:
    """Genera, compara y sube. Lanza PublishError si algo impide publicar."""
    salida = salida or SALIDA
    if dry_run:
        ics = build_calendar(deadlines, now=now, state=_EstadoSoloLectura(store.all()))
    else:
        ics = build_calendar(deadlines, now=now, state=store)

    eventos = ics.count("BEGIN:VEVENT")
    sha = huella(ics)
    log.info(
        "%d fechas limite en el horizonte: %d eventos, %s. sha256=%s",
        len(deadlines),
        eventos,
        _resumen_exclusiones(deadlines),
        sha,
    )

    if dry_run:
        log.info("--dry-run: no se escribe build/, ni SQLite, ni se sube nada.")
        (stdout or _stdout_bytes)(ics.encode("utf-8"))
        return

    escribir_salida(ics, salida)
    log.info("Escrito %s (%d bytes).", salida, len(ics.encode("utf-8")))

    if store.last_uploaded_sha256(FEED) == sha:
        log.info("Sin cambios desde la ultima subida: no se sube nada.")
        return

    url = os.environ.get(FEED_URL_ENV, "").strip()
    if not url.startswith("https://"):
        raise PublishError(
            f"Falta ${FEED_URL_ENV} o no es https (p.ej. https://<worker>.workers.dev/{FEED}.ics)."
        )
    token = (obtener_token or leer_token)()
    censura = _Censura(token)
    log.addFilter(censura)
    try:
        propio = cliente is None
        http = cliente or _nuevo_cliente()
        try:
            intentos = subir(url, ics.encode("utf-8"), token, cliente=http, dormir=dormir)
        finally:
            if propio:
                http.close()
    finally:
        log.removeFilter(censura)

    store.record_upload(FEED, sha, now)
    log.info("Subido a %s en %d intento(s).", httpx2.URL(url).host, intentos)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="upv-publish",
        description="Genera build/entregas.ics con las entregas pendientes y lo sube al Worker.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_DAYS,
        help=f"Dias hacia adelante (1-{deadlines_tool.MAX_DAYS_AHEAD}). "
        f"Por defecto {DEFAULT_DAYS}.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Imprime el .ics por stdout. No escribe build/, ni el estado SQLite, ni sube.",
    )
    args = parser.parse_args(argv)
    _configurar_logging(dry_run=args.dry_run)

    inicio = time.monotonic()
    modo = ", dry-run" if args.dry_run else ""
    log.info("upv-publish: inicio (horizonte %d dias%s).", args.days, modo)
    try:
        settings = load_settings()
        settings.ensure_dirs()
        with CacheRepository(settings.db_path, settings.timezone) as cache:
            repo = AcademicRepository(settings, cache)
            deadlines = asyncio.run(obtener_deadlines(repo, args.days))
        with SqliteEventStateStore(settings.db_path) as store:
            publicar(deadlines, store, now=datetime.now(UTC), dry_run=args.dry_run)
    except (PublishError, SourceError, OSError, ValueError) as exc:
        # Solo el mensaje, sin traceback: es lo que se lee en el log de launchd.
        log.error("%s: %s", type(exc).__name__, exc)
        log.error("upv-publish: FALLO tras %.1f s.", time.monotonic() - inicio)
        return 1
    log.info("upv-publish: fin en %.1f s.", time.monotonic() - inicio)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
