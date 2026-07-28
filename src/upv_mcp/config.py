"""Configuracion del servidor.

Sin secretos hardcoded y sin secretos en fichero plano: la v0 solo necesita una URL
de suscripcion iCal (o un fichero local). Cuando entre PoliformaT en la v1, las
credenciales se leeran de `keyring` (Keychain en macOS), nunca de aqui.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_NAME: Final = "upv-mcp"

#: Toda fecha/hora se normaliza a esta zona al parsear, nunca en la capa de tools.
DEFAULT_TIMEZONE: Final = "Europe/Madrid"

#: User-Agent identificable. Requisito de buena ciudadania cuando llegue PoliformaT.
USER_AGENT: Final = f"{APP_NAME}/0.1 (+https://github.com/pgomsan/upv-mcp)"


def _xdg_data_home() -> Path:
    """Directorio de datos segun XDG, con el fallback habitual en macOS/Linux."""
    if raw := os.environ.get("XDG_DATA_HOME"):
        return Path(raw)
    return Path.home() / ".local" / "share"


def _xdg_config_home() -> Path:
    if raw := os.environ.get("XDG_CONFIG_HOME"):
        return Path(raw)
    return Path.home() / ".config"


class Settings(BaseSettings):
    """Ajustes del servidor, sobreescribibles por entorno con el prefijo ``UPV_MCP_``."""

    model_config = SettingsConfigDict(
        env_prefix="UPV_MCP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Origen de datos (v0: solo .ics) ---------------------------------------
    ics_url: str | None = Field(
        default=None,
        description="URL de suscripcion iCal del horario UPV.",
    )
    ics_file: Path | None = Field(
        default=None,
        description="Ruta a un .ics local. Alternativa a ics_url, util sin red.",
    )

    # --- Cache ------------------------------------------------------------------
    data_dir: Path = Field(default_factory=lambda: _xdg_data_home() / APP_NAME)
    cache_ttl_seconds: int = Field(default=6 * 60 * 60, ge=0)

    # --- Red --------------------------------------------------------------------
    http_timeout_seconds: float = Field(default=15.0, gt=0)
    http_max_attempts: int = Field(default=3, ge=1)
    http_backoff_base_seconds: float = Field(default=0.5, gt=0)

    # --- Limites de respuesta ---------------------------------------------------
    max_results: int = Field(
        default=50,
        ge=1,
        description="Tope de elementos por respuesta. Al superarse se trunca y se avisa.",
    )

    timezone: str = Field(default=DEFAULT_TIMEZONE)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "cache.db"

    @property
    def config_dir(self) -> Path:
        return _xdg_config_home() / APP_NAME

    @model_validator(mode="after")
    def _require_a_source(self) -> Settings:
        """Sin origen no hay servidor: se falla al arrancar, no en la primera tool."""
        if self.ics_url is None and self.ics_file is None:
            raise ValueError(
                "Falta el origen de datos: define UPV_MCP_ICS_URL con tu URL de "
                "suscripcion iCal de la UPV, o UPV_MCP_ICS_FILE con un .ics local."
            )
        return self

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
