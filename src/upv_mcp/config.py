"""Configuracion del servidor.

Las URL iCal de la UPV llevan un token de 96 caracteres en la propia URL: quien la
tiene, lee tu horario completo con tu nombre. Por eso son credenciales y viven en el
llavero del sistema (Keychain en macOS), no en un fichero. La variable de entorno
existe solo como fallback para CI y para tests.

Resolucion, en orden: variable de entorno -> keyring -> ausente.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_NAME: Final = "upv-mcp"

#: Servicio bajo el que se guardan los secretos en el llavero.
KEYRING_SERVICE: Final = APP_NAME

#: Toda fecha/hora se normaliza a esta zona al parsear, nunca en la capa de tools.
DEFAULT_TIMEZONE: Final = "Europe/Madrid"

#: User-Agent identificable. Buena ciudadania de cara a PoliformaT en la v1.
USER_AGENT: Final = f"{APP_NAME}/0.1 (+https://github.com/pgomsan/upv-mcp)"

#: Claves de los calendarios que entiende el servidor.
SCHEDULE_KEY: Final = "schedule_ics_url"
EXAMS_KEY: Final = "exams_ics_url"

#: Credenciales de PoliformaT (v1). Nunca salen del llavero.
POLIFORMAT_USER_KEY: Final = "poliformat_username"
POLIFORMAT_PASSWORD_KEY: Final = "poliformat_password"


def _xdg_data_home() -> Path:
    if raw := os.environ.get("XDG_DATA_HOME"):
        return Path(raw)
    return Path.home() / ".local" / "share"


def _xdg_config_home() -> Path:
    if raw := os.environ.get("XDG_CONFIG_HOME"):
        return Path(raw)
    return Path.home() / ".config"


def read_secret(key: str) -> str | None:
    """Lee un secreto del llavero. Devuelve None si no hay backend o no existe.

    El import de keyring es perezoso a proposito: en un entorno sin llavero (CI,
    contenedor) el servidor debe seguir arrancando con variables de entorno.
    """
    try:
        import keyring
    except ImportError:  # pragma: no cover - keyring es dependencia directa
        return None
    try:
        return keyring.get_password(KEYRING_SERVICE, key)
    except Exception:
        # Llavero bloqueado o backend ausente: no es fatal, hay fallback por entorno.
        return None


def write_secret(key: str, value: str) -> None:
    import keyring

    keyring.set_password(KEYRING_SERVICE, key, value)


def delete_secret(key: str) -> None:
    import contextlib

    import keyring

    # Borrar algo que no existe no es un error.
    with contextlib.suppress(Exception):
        keyring.delete_password(KEYRING_SERVICE, key)


class CalendarSpec(BaseModel):
    """Un calendario a ingerir. La v0 admite varios: horario y, si existe, examenes."""

    name: str = Field(description="Identificador corto, p.ej. 'schedule' o 'exams'.")
    url: str | None = None
    path: Path | None = None

    @property
    def origin(self) -> str:
        """Descripcion no sensible del origen, apta para logs y mensajes de error."""
        if self.path is not None:
            return str(self.path)
        return "url-remota"


class Settings(BaseSettings):
    """Ajustes del servidor, sobreescribibles por entorno con el prefijo ``UPV_MCP_``."""

    model_config = SettingsConfigDict(
        env_prefix="UPV_MCP_",
        env_file=None,
        extra="ignore",
    )

    # --- Origenes de datos (v0: solo .ics) -------------------------------------
    schedule_ics_url: str | None = Field(default=None, description="URL iCal del horario.")
    exams_ics_url: str | None = Field(default=None, description="URL iCal de examenes.")
    schedule_ics_file: Path | None = Field(default=None, description="Horario en fichero local.")
    exams_ics_file: Path | None = Field(default=None, description="Examenes en fichero local.")

    # --- PoliformaT / Sakai (v1) ------------------------------------------------
    poliformat_base_url: str = Field(default="https://poliformat.upv.es")
    cas_base_url: str = Field(
        default="https://cas.upv.es",
        description="SSO de la UPV. PoliformaT tiene deshabilitado el login por API.",
    )
    poliformat_username: str | None = Field(default=None)
    poliformat_password: SecretStr | None = Field(
        default=None,
        description="SecretStr para que no aparezca en repr(), logs ni tracebacks.",
    )
    poliformat_rate_per_second: float = Field(
        default=0.5,
        gt=0,
        description="Conservador a proposito: Sakai es infraestructura compartida.",
    )

    # --- Cache ------------------------------------------------------------------
    data_dir: Path = Field(default_factory=lambda: _xdg_data_home() / APP_NAME)
    cache_ttl_seconds: int = Field(default=6 * 60 * 60, ge=0)

    # --- Red --------------------------------------------------------------------
    http_timeout_seconds: float = Field(default=15.0, gt=0)
    http_max_attempts: int = Field(default=3, ge=1)
    http_backoff_base_seconds: float = Field(default=0.5, gt=0)
    http_min_interval_seconds: float = Field(
        default=1.0, ge=0, description="Intervalo minimo entre peticiones al mismo host."
    )

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

    @property
    def calendars(self) -> list[CalendarSpec]:
        """Calendarios configurados, en orden de prioridad."""
        specs: list[CalendarSpec] = []
        if self.schedule_ics_url or self.schedule_ics_file:
            specs.append(
                CalendarSpec(
                    name="schedule",
                    url=self.schedule_ics_url,
                    path=self.schedule_ics_file,
                )
            )
        if self.exams_ics_url or self.exams_ics_file:
            specs.append(
                CalendarSpec(name="exams", url=self.exams_ics_url, path=self.exams_ics_file)
            )
        return specs

    @property
    def poliformat_enabled(self) -> bool:
        """PoliformaT solo se activa si hay credenciales completas."""
        return bool(self.poliformat_username and self.poliformat_password)

    @property
    def has_exam_calendar(self) -> bool:
        """Si es False, `list_upcoming_deadlines` no tiene de donde sacar examenes."""
        return any(c.name == "exams" for c in self.calendars)

    @model_validator(mode="after")
    def _require_a_source(self) -> Settings:
        """Sin origen no hay servidor: se falla al arrancar, no dentro de una tool."""
        if not self.calendars:
            raise ValueError(
                "Falta el origen de datos. Configura tu URL iCal de la UPV con:\n"
                "    upv-mcp-config set schedule\n"
                "o exporta UPV_MCP_SCHEDULE_ICS_URL / UPV_MCP_SCHEDULE_ICS_FILE."
            )
        return self

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    """Construye Settings resolviendo los secretos del llavero.

    El entorno gana al llavero para que un test o un CI puedan forzar el origen sin
    tocar el Keychain del usuario.
    """
    overrides: dict[str, str] = {}
    resolvable = (
        ("schedule_ics_url", SCHEDULE_KEY),
        ("exams_ics_url", EXAMS_KEY),
        ("poliformat_username", POLIFORMAT_USER_KEY),
        ("poliformat_password", POLIFORMAT_PASSWORD_KEY),
    )
    for field, key in resolvable:
        env_name = f"UPV_MCP_{field.upper()}"
        if os.environ.get(env_name):
            continue
        if secret := read_secret(key):
            overrides[field] = secret
    return Settings(**overrides)  # type: ignore[arg-type]
