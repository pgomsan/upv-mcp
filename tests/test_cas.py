"""Tests de la autenticacion CAS.

Todo con transporte simulado: los tests no hablan con cas.upv.es. Lo critico aqui
no es el camino feliz sino el de fallo, porque reintentar contra un SSO
universitario bloquea la cuenta.
"""

from __future__ import annotations

import httpx2
import pytest
from pydantic import SecretStr

from upv_mcp.sources import cas
from upv_mcp.sources.base import RetryableError

SERVICE = "https://poliformat.upv.es/sakai-login-tool/container"

_FORMULARIO = """
<html><body>
<form method="post" id="fm1" action="login" target="_top">
  <input type="text" name="username" value="">
  <input type="password" name="password" value="">
  <input type="hidden" name="execution" value="TOKEN-EXECUTION-123">
  <input type="hidden" name="_eventId" value="submit">
  <input type="hidden" name="_csrf" value="TOKEN-CSRF-456">
</form>
<form method="post" id="formCl@ve" action="/cas/login">
  <input type="hidden" name="execution" value="EXECUTION-DE-CLAVE-NO-USAR">
  <input type="hidden" name="client_name" value="Cl@ve">
</form>
</body></html>
"""

_EXITO = "<html><body>Redirigiendo a PoliformaT...</body></html>"


def _cliente(handler: object) -> httpx2.AsyncClient:
    transport = httpx2.MockTransport(handler)  # type: ignore[arg-type]
    return httpx2.AsyncClient(transport=transport, follow_redirects=True)


def test_extrae_los_campos_del_formulario_correcto() -> None:
    """La pagina trae tambien el form de Cl@ve, cuyo execution NO sirve."""
    campos = cas._hidden_fields(_FORMULARIO)

    assert campos["execution"] == "TOKEN-EXECUTION-123"
    assert campos["_csrf"] == "TOKEN-CSRF-456"
    assert "client_name" not in campos


def test_formulario_ausente_da_error_accionable() -> None:
    with pytest.raises(cas.CasError, match="sources/cas.py"):
        cas._hidden_fields("<html><body>mantenimiento</body></html>")


async def test_login_correcto_envia_los_tokens() -> None:
    enviados: dict[str, str] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.method == "GET":
            return httpx2.Response(200, text=_FORMULARIO)
        cuerpo = request.content.decode()
        for par in cuerpo.split("&"):
            k, _, v = par.partition("=")
            enviados[k] = v
        return httpx2.Response(200, text=_EXITO, request=request)

    async with _cliente(handler) as client:
        await cas.authenticate(
            client,
            cas_base_url="https://cas.upv.es",
            service_url=SERVICE,
            username="alumno",
            password=SecretStr("secreta"),
        )

    assert enviados["execution"] == "TOKEN-EXECUTION-123"
    assert enviados["_csrf"] == "TOKEN-CSRF-456"
    assert enviados["_eventId"] == "submit"
    assert enviados["username"] == "alumno"


async def test_credenciales_rechazadas_no_se_reintentan() -> None:
    """CAS devuelve 200 con el formulario otra vez, no un 401.

    Si esto se tomara por exito, el error saldria despues y confuso. Y si se
    reintentara, se bloquearia la cuenta.
    """
    intentos = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal intentos
        if request.method == "POST":
            intentos += 1
        return httpx2.Response(200, text=_FORMULARIO, request=request)

    async with _cliente(handler) as client:
        with pytest.raises(cas.CasError, match="rechazo las credenciales"):
            await cas.authenticate(
                client,
                cas_base_url="https://cas.upv.es",
                service_url=SERVICE,
                username="alumno",
                password=SecretStr("mala"),
            )

    assert intentos == 1, "no debe reintentarse un login rechazado"


async def test_cuenta_bloqueada_se_distingue() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.method == "GET":
            return httpx2.Response(200, text=_FORMULARIO)
        return httpx2.Response(200, text="<p>Su cuenta esta bloqueada</p>", request=request)

    async with _cliente(handler) as client:
        with pytest.raises(cas.CasAccountLocked, match="bloqueada"):
            await cas.authenticate(
                client,
                cas_base_url="https://cas.upv.es",
                service_url=SERVICE,
                username="alumno",
                password=SecretStr("x"),
            )


async def test_fallo_de_red_si_es_reintentable() -> None:
    """Un corte de red no es culpa de las credenciales: eso si se reintenta."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("sin red", request=request)

    async with _cliente(handler) as client:
        with pytest.raises(RetryableError):
            await cas.authenticate(
                client,
                cas_base_url="https://cas.upv.es",
                service_url=SERVICE,
                username="alumno",
                password=SecretStr("x"),
            )


async def test_la_contrasena_no_aparece_en_el_error() -> None:
    """Un traceback no debe filtrar la contrasena a un log."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, text=_FORMULARIO, request=request)

    async with _cliente(handler) as client:
        with pytest.raises(cas.CasError) as info:
            await cas.authenticate(
                client,
                cas_base_url="https://cas.upv.es",
                service_url=SERVICE,
                username="alumno",
                password=SecretStr("SUPERSECRETA123"),
            )

    assert "SUPERSECRETA123" not in str(info.value)
