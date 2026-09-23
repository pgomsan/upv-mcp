"""Autenticacion contra el CAS de la UPV (Apereo CAS).

PoliformaT tiene deshabilitado el login por API: `POST /direct/session` devuelve 403
("Security exception handling request for view (/session/new)") incluso con un
usuario inexistente, y `/sakai-login-tool/container` redirige a `cas.upv.es`. La
unica via es reproducir el flujo CAS.

Flujo (CAS 6.x con webflow):

    1. GET  /cas/login?service=<url de Sakai>   -> formulario con `execution` y `_csrf`
    2. POST /cas/login  (username, password, execution, _csrf, _eventId=submit)
    3. CAS redirige al `service` con un ticket ST-...
    4. Sakai valida el ticket y entrega su cookie de sesion

SEGURIDAD, y no es negociable: si CAS rechaza las credenciales NO se reintenta
jamas. Un bucle de reintentos contra un SSO universitario bloquea la cuenta. Solo
los fallos de red son reintentables, y de eso se encarga la capa de arriba.
"""

from __future__ import annotations

import re
from typing import Final

import httpx2
from pydantic import SecretStr

from upv_mcp.sources.base import RetryableError, SourceError

#: El formulario de usuario/contrasena de CAS. La pagina trae ademas formularios de
#: Cl@ve y de certificado X509, cuyos tokens NO sirven.
_LOGIN_FORM: Final = re.compile(
    r'<form[^>]*id="fm1"[^>]*>(?P<cuerpo>.*?)</form>', re.IGNORECASE | re.DOTALL
)
_INPUT: Final = re.compile(r"<input[^>]*>", re.IGNORECASE)
_ATTR: Final = re.compile(r'(?P<attr>name|value)="(?P<valor>[^"]*)"', re.IGNORECASE)

#: Marcas de que CAS ha devuelto el formulario otra vez, es decir, login fallido.
_ERROR_HINTS: Final = re.compile(
    r"(credenciales no son validas|credenciales no son v\S+lidas|invalid credentials"
    r"|authentication attempt has failed|error de autenticaci)",
    re.IGNORECASE,
)


class CasError(SourceError):
    """Fallo de autenticacion en CAS. NO se reintenta."""


class CasAccountLocked(CasError):
    """CAS indica cuenta bloqueada o deshabilitada."""


class CasCredentialsRejected(CasError):
    """CAS rechazo usuario o contrasena. Repetirlo es lo que bloquea la cuenta."""


def _hidden_fields(html: str) -> dict[str, str]:
    """Extrae los campos ocultos del formulario de login (solo de `fm1`)."""
    match = _LOGIN_FORM.search(html)
    if match is None:
        raise CasError(
            "No se encontro el formulario de login de CAS. Es probable que la UPV haya "
            "cambiado la pagina de acceso y haya que revisar sources/cas.py."
        )

    campos: dict[str, str] = {}
    for tag in _INPUT.findall(match.group("cuerpo")):
        atributos = {m.group("attr").lower(): m.group("valor") for m in _ATTR.finditer(tag)}
        nombre = atributos.get("name")
        if nombre and nombre not in campos:
            campos[nombre] = atributos.get("value", "")
    return campos


async def authenticate(
    client: httpx2.AsyncClient,
    *,
    cas_base_url: str,
    service_url: str,
    username: str,
    password: SecretStr,
) -> None:
    """Autentica al usuario y deja la cookie de sesion en el jar de `client`.

    `client` debe ser el mismo que usara despues la API, para que las cookies de
    CAS y de Sakai convivan en el mismo jar.
    """
    login_url = f"{cas_base_url.rstrip('/')}/cas/login"

    try:
        pagina = await client.get(login_url, params={"service": service_url})
    except httpx2.RequestError as exc:
        raise RetryableError(f"No se pudo contactar con el CAS de la UPV: {exc}") from exc

    if pagina.status_code >= 400:
        raise RetryableError(f"El CAS respondio {pagina.status_code} al pedir el formulario")

    campos = _hidden_fields(pagina.text)
    if "execution" not in campos:
        raise CasError(
            "El formulario de CAS no trae el token 'execution'. La pagina de login ha "
            "cambiado; hay que revisar sources/cas.py."
        )

    payload = {
        **{k: v for k, v in campos.items() if k not in ("username", "password")},
        "username": username,
        "password": password.get_secret_value(),
        "_eventId": "submit",
    }

    try:
        respuesta = await client.post(
            login_url,
            params={"service": service_url},
            data=payload,
            headers={"Referer": str(pagina.url)},
        )
    except httpx2.RequestError as exc:
        raise RetryableError(f"Fallo de red enviando credenciales a CAS: {exc}") from exc

    _comprobar_resultado(respuesta, cas_base_url)


def _comprobar_resultado(respuesta: httpx2.Response, cas_base_url: str) -> None:
    """Distingue login correcto de credenciales rechazadas.

    CAS no usa 401: cuando falla, devuelve 200 con el formulario otra vez. Si se
    tomara eso por exito, la siguiente peticion a la API daria un error confuso.
    """
    cuerpo = respuesta.text
    host_cas = httpx2.URL(cas_base_url).host

    if "bloquead" in cuerpo.lower() or "locked" in cuerpo.lower():
        raise CasAccountLocked(
            "CAS indica que la cuenta esta bloqueada. Entra manualmente en "
            "https://intranet.upv.es para desbloquearla. NO se reintentara."
        )

    seguimos_en_cas = respuesta.url.host == host_cas
    hay_formulario = _LOGIN_FORM.search(cuerpo) is not None

    if seguimos_en_cas and (hay_formulario or _ERROR_HINTS.search(cuerpo)):
        raise CasCredentialsRejected(
            "CAS rechazo las credenciales. Revisalas con `upv-mcp-config set poliformat`.\n"
            "No se reintenta a proposito: repetir intentos fallidos contra el SSO de la "
            "UPV puede bloquear tu cuenta."
        )

    if respuesta.status_code >= 400:
        raise CasError(f"CAS respondio {respuesta.status_code} al autenticar.")
