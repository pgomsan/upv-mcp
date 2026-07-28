"""Contrato de un origen de datos academicos.

INVARIANTE DE ARQUITECTURA: este paquete no importa nada de `mcp`. Una fuente solo
sabe de red y de parseo, y produce modelos de `upv_mcp.models`. Asi se puede anadir
PoliformaT en la v1 sin tocar la capa de tools.

Aqui viven tambien las piezas de buena ciudadania de red (RateLimiter y retry con
backoff). La v0 apenas las necesita -- descarga un .ics cada varias horas -- pero
PoliformaT si, y es mejor que el contrato exista desde el principio que anadirlo
con prisa cuando ya haya scraping.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from upv_mcp.models import Announcement, Assignment, ClassSession, Material


class SourceError(RuntimeError):
    """Fallo no recuperable al obtener datos de un origen."""


class RetryableError(SourceError):
    """Fallo transitorio (red, 5xx, 429). Merece reintento con backoff."""

    def __init__(self, message: str, *, retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class SourcePayload:
    """Lo que devuelve una fuente.

    Una fuente rellena solo lo que sabe: el .ics trae sesiones, PoliformaT trae
    entregas, materiales y anuncios.
    """

    sessions: list[ClassSession] = field(default_factory=list)
    assignments: list[Assignment] = field(default_factory=list)
    materials: list[Material] = field(default_factory=list)
    announcements: list[Announcement] = field(default_factory=list)
    fetched_at: datetime | None = None
    stale: bool = False


@runtime_checkable
class AcademicSource(Protocol):
    """Origen de datos academicos.

    Implementaciones: `IcsSource` (v0) y `PoliformatSource` (v1).
    """

    @property
    def name(self) -> str:
        """Identificador corto del origen, para logs y para el campo `source`."""
        ...

    async def fetch(self, *, force_refresh: bool = False) -> SourcePayload:
        """Devuelve los datos del origen, usando cache si procede.

        Debe degradar en vez de reventar: si la red falla pero hay cache, devuelve
        la cache con `stale=True`.
        """
        ...


class RateLimiter:
    """Token bucket asincrono, conservador por defecto.

    Sin uso real en la v0 (un .ics cada varias horas no estresa a nadie), pero es la
    pieza que PoliformaT necesitara para no martillear a Sakai.
    """

    def __init__(self, rate_per_second: float = 1.0, burst: int = 1) -> None:
        if rate_per_second <= 0:
            raise ValueError("rate_per_second debe ser > 0")
        if burst < 1:
            raise ValueError("burst debe ser >= 1")
        self._rate = rate_per_second
        self._burst = float(burst)
        self._tokens = float(burst)
        # El reloj es el del event loop, que no existe hasta el primer acquire().
        self._updated = 0.0
        self._lock = asyncio.Lock()
        self._initialised = False

    async def acquire(self) -> None:
        """Espera lo necesario para respetar el ritmo configurado."""
        loop = asyncio.get_running_loop()
        async with self._lock:
            now = loop.time()
            if not self._initialised:
                self._updated = now
                self._initialised = True
            self._tokens = min(self._burst, self._tokens + (now - self._updated) * self._rate)
            self._updated = now
            if self._tokens < 1.0:
                wait = (1.0 - self._tokens) / self._rate
                await asyncio.sleep(wait)
                self._tokens = 0.0
                self._updated = loop.time()
            else:
                self._tokens -= 1.0


async def with_retry[T](
    operation: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 30.0,
) -> T:
    """Ejecuta `operation` reintentando los `RetryableError` con backoff exponencial.

    El jitter evita que varios clientes reintenten a la vez. Si el servidor manda
    `Retry-After`, se respeta ese valor en lugar del backoff calculado.
    """
    if attempts < 1:
        raise ValueError("attempts debe ser >= 1")

    last: RetryableError | None = None
    for attempt in range(attempts):
        try:
            return await operation()
        except RetryableError as exc:
            last = exc
            if attempt == attempts - 1:
                break
            if exc.retry_after_seconds is not None:
                delay = min(exc.retry_after_seconds, max_delay)
            else:
                delay = min(base_delay * (2**attempt), max_delay)
                delay *= 0.5 + random.random()  # noqa: S311 - jitter, no es criptografia
            await asyncio.sleep(delay)

    assert last is not None
    raise SourceError(f"Agotados {attempts} intentos: {last}") from last
