"""Origen PoliformaT (Sakai). STUB: no implementado en la v0.

La interfaz existe ya para fijar el contrato y demostrar que anadir esta fuente en
la v1 NO obliga a tocar `tools/`: bastara con instanciar esta clase junto a
`IcsSource` y agregar sus resultados.

TODO(v1): implementar contra https://poliformat.upv.es
  1. Autenticacion: login UPV. Las credenciales se leen SIEMPRE del llavero del
     sistema (`upv_mcp.config.read_secret`), nunca de fichero ni de .env.
  2. Entregas: herramienta "Tareas" de Sakai -> modelos `Assignment`.
  3. Materiales: herramienta "Recursos" -> modelos `Material`, expuestos como MCP
     resources (no como tool) porque son contenido navegable, no una accion.
  4. Rate limiting conservador con el `RateLimiter` de `base.py` (<= 1 req/s),
     User-Agent identificable (`config.USER_AGENT`) y acceso unicamente a los datos
     de la propia cuenta.
  5. Respetar robots.txt y cachear agresivamente: Sakai es lento y es de la UPV.
"""

from __future__ import annotations

from upv_mcp.config import Settings
from upv_mcp.models import SourceName
from upv_mcp.sources.base import RateLimiter, SourcePayload

_NOT_IMPLEMENTED = (
    "PoliformaT no esta implementado en la v0. Esta version se alimenta solo del "
    "export .ics del calendario UPV. Las entregas llegaran en la v1."
)


class PoliformatSource:
    """Stub del origen PoliformaT. Cumple el Protocol `AcademicSource`."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        # Deliberadamente conservador: Sakai es infraestructura compartida.
        self._limiter = RateLimiter(rate_per_second=0.5, burst=1)

    @property
    def name(self) -> str:
        return SourceName.POLIFORMAT.value

    async def fetch(self, *, force_refresh: bool = False) -> SourcePayload:
        raise NotImplementedError(_NOT_IMPLEMENTED)
