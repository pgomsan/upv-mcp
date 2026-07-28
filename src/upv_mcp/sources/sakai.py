"""Cliente HTTP de la API REST de Sakai (EntityBroker) de PoliformaT.

PoliformaT expone `/direct/`, la API REST oficial de Sakai, con JSON. No hace falta
scraping: es mas estable, mas rapido y mucho mejor ciudadania que parsear HTML.

Autenticacion: `POST /direct/session` con `_username` y `_password` devuelve una
cookie de sesion que se reutiliza en el resto de peticiones. El cliente mantiene el
cookie jar de httpx2 durante toda su vida.

INVARIANTE: este modulo no importa nada de `mcp`. Solo red.

Buena ciudadania, no opcional:
- Rate limiting conservador (0.5 req/s por defecto). Sakai es infraestructura
  compartida de la universidad.
- User-Agent identificable.
- Timeout explicito y reintentos con backoff solo en fallos transitorios.
- Acceso unicamente a los datos de la propia cuenta autenticada.
"""

from __future__ import annotations

from types import TracebackType
from typing import Any

import httpx2

from upv_mcp.config import USER_AGENT, Settings
from upv_mcp.sources import cas
from upv_mcp.sources.base import RateLimiter, RetryableError, SourceError, with_retry


class AuthError(SourceError):
    """Credenciales rechazadas o sesion no valida. Reintentar no lo arregla."""


class SakaiClient:
    """Cliente autenticado contra `/direct/` de PoliformaT."""

    def __init__(self, settings: Settings) -> None:
        if not settings.poliformat_enabled:
            raise AuthError(
                "Faltan las credenciales de PoliformaT. Configuralas con:\n"
                "    upv-mcp-config set poliformat"
            )
        self._settings = settings
        self._base = settings.poliformat_base_url.rstrip("/")
        self._limiter = RateLimiter(rate_per_second=settings.poliformat_rate_per_second)
        self._client: httpx2.AsyncClient | None = None
        self._session_id: str | None = None

    # -- Ciclo de vida ------------------------------------------------------------

    async def __aenter__(self) -> SakaiClient:
        self._client = httpx2.AsyncClient(
            base_url=self._base,
            timeout=self._settings.http_timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
        await self.login()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            await self.logout()
        finally:
            if self._client is not None:
                await self._client.aclose()
                self._client = None

    @property
    def _http(self) -> httpx2.AsyncClient:
        if self._client is None:
            raise SourceError("El cliente Sakai se usa fuera de su contexto (`async with`).")
        return self._client

    @property
    def session_id(self) -> str | None:
        return self._session_id

    # -- Autenticacion ------------------------------------------------------------

    async def login(self) -> None:
        """Abre sesion via CAS y deja la cookie de Sakai en el jar del cliente.

        `POST /direct/session` (el login nativo de Sakai) esta deshabilitado en
        PoliformaT: devuelve 403 incluso con usuarios inexistentes. Ver sources/cas.py.

        La contrasena solo se materializa dentro del POST a CAS y no se registra en
        ningun sitio.
        """
        password = self._settings.poliformat_password
        assert password is not None  # garantizado por poliformat_enabled

        await self._limiter.acquire()
        await cas.authenticate(
            self._http,
            cas_base_url=self._settings.cas_base_url,
            service_url=f"{self._base}/sakai-login-tool/container",
            username=self._settings.poliformat_username or "",
            password=password,
        )

        # CAS puede redirigir sin que Sakai llegue a abrir sesion. Se confirma
        # pidiendo la sesion actual antes de dar el login por bueno.
        sesion = await self.get_json("/direct/session/current.json")
        self._session_id = str(sesion.get("id") or "") or None
        if not sesion.get("userEid") and not sesion.get("userId"):
            raise AuthError(
                "CAS acepto el login pero Sakai no abrio sesion de usuario. "
                "Puede que el ticket no se haya validado."
            )

    async def logout(self) -> None:
        """Cierra la sesion. Un fallo aqui no debe romper nada."""
        if self._client is None or self._session_id is None:
            return
        try:
            await self._http.delete(f"/direct/session/{self._session_id}")
        except (httpx2.RequestError, httpx2.HTTPError):
            pass
        finally:
            self._session_id = None

    # -- Peticiones ---------------------------------------------------------------

    async def get_json(self, path: str, **params: Any) -> Any:  # noqa: ANN401 - JSON arbitrario
        """GET a `/direct/...` devolviendo JSON, con rate limit y reintentos."""
        return await with_retry(
            lambda: self._get_json_once(path, params),
            attempts=self._settings.http_max_attempts,
            base_delay=self._settings.http_backoff_base_seconds,
        )

    async def _get_json_once(self, path: str, params: dict[str, Any]) -> Any:  # noqa: ANN401
        await self._limiter.acquire()
        try:
            response = await self._http.get(path, params=params)
        except httpx2.TimeoutException as exc:
            raise RetryableError(f"Timeout pidiendo {path}") from exc
        except httpx2.RequestError as exc:
            raise RetryableError(f"Error de red pidiendo {path}: {exc}") from exc

        if response.status_code in (401, 403):
            raise AuthError(f"Sesion no valida o sin permisos para {path}.")
        if response.status_code == 429 or response.status_code >= 500:
            retry_after = response.headers.get("Retry-After")
            raise RetryableError(
                f"{path} respondio {response.status_code}",
                retry_after_seconds=float(retry_after)
                if retry_after and retry_after.isdigit()
                else None,
            )
        if response.status_code >= 400:
            raise SourceError(f"{path} respondio {response.status_code}")

        try:
            return response.json()
        except ValueError as exc:
            # Sakai devuelve la pagina de login en HTML cuando la sesion caduca.
            raise AuthError(
                f"{path} no devolvio JSON. Lo mas probable es que la sesion haya caducado."
            ) from exc
