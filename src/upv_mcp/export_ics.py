"""Exportacion de las entregas de PoliformaT a un calendario iCalendar (RFC 5545).

Pensado para suscribirse desde el movil: cada entrega pendiente aparece como un
evento de todo el dia en su fecha limite, con la hora real en el titulo.

Es una funcion pura: recibe los modelos ya normalizados y devuelve el texto. No
toca red, cache ni ficheros, y no sabe nada de MCP.

Se escribe a mano, sin `icalendar`, porque lo que importa aqui son los bytes
exactos: un cliente suscrito compara por UID y el golden test compara byte a byte.
Las tres reglas de RFC 5545 que hay que respetar a mano:

* Escapado de texto (3.3.11): barra invertida, punto y coma, coma y salto de linea.
* Plegado (3.1): ninguna linea pasa de 75 OCTETOS sin contar el CRLF. Con acentos
  en UTF-8 eso son menos de 75 caracteres, y nunca se corta dentro de un caracter.
* Terminador CRLF en todas las lineas, incluida la ultima.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from upv_mcp.models import Assignment, EventKind, SubmissionStatus

#: Sufijo del UID de cada evento. NO SE PUEDE CAMBIAR NUNCA: los clientes suscritos
#: identifican los eventos por UID, y si cambia, cada iPhone suscrito duplicara
#: todos los eventos (los viejos no desaparecen, se suman los nuevos).
UID_DOMAIN = "upv-mcp.local"

#: Siglas por codigo de asignatura. Es solo una red de seguridad: normalmente el
#: acronimo ya llega en `course.acronym`, porque el repositorio sustituye la
#: asignatura de PoliformaT por la del horario .ics (ver `_nombres_reales` en
#: repository.py). Solo hace falta para asignaturas sin clases en el horario.
ACRONIMOS: dict[str, str] = {
    "14534": "ICD",
    "14540": "CBI",
    "14548": "AAU",
    "14551": "DID",
    "14553": "AID",
    "14556": "MCR",
}

#: Descripcion corta por uid de PoliformaT (el `Assignment.uid` sin sufijo). Si una
#: tarea no esta aqui, se usa su titulo. Se rellena a mano.
DESCRIPCIONES: dict[str, str] = {}

PRODID = "-//upv-mcp//Entregas UPV//ES"
CALNAME = "Entregas UPV"

_TZ = ZoneInfo("Europe/Madrid")
_MAX_OCTETOS = 75
_CRLF = "\r\n"


def build_calendar(deadlines: Sequence[Assignment], *, now: datetime) -> str:
    """Devuelve un VCALENDAR con un evento de todo el dia por entrega publicable.

    `now` solo se usa para DTSTAMP: con el mismo `now` y los mismos datos, la salida
    es identica byte a byte. Los eventos se ordenan por fecha limite y uid, asi que
    tampoco depende del orden de entrada.
    """
    if now.tzinfo is None:
        raise ValueError("now debe llevar zona horaria.")
    dtstamp = now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")

    lineas = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{_escapar(PRODID)}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escapar(CALNAME)}",
        "X-PUBLISHED-TTL:PT1H",
    ]
    publicables = sorted(
        (a for a in deadlines if a.kind is EventKind.ASSIGNMENT and _debe_publicarse(a)),
        key=lambda a: (a.due, a.uid),
    )
    for entrega in publicables:
        lineas.extend(_vevent(entrega, dtstamp))
    lineas.append("END:VCALENDAR")

    return "".join(_plegar(linea) + _CRLF for linea in lineas)


def _debe_publicarse(assignment: Assignment) -> bool:
    """Lista de exclusion: fuera lo entregado y lo ya corregido; todo lo demas entra.

    Sin submission o con estado "unknown" se publica: no saber si esta hecha no es
    motivo para esconder la fecha. Lo corregido se excluye aunque el estado diga
    "not_submitted" (PoliformaT produce esa contradiccion): una tarea con nota esta
    cerrada.
    """
    entrega = assignment.submission
    if entrega is None:
        return True
    return not (entrega.status is SubmissionStatus.SUBMITTED or entrega.graded)


def _vevent(entrega: Assignment, dtstamp: str) -> list[str]:
    if entrega.due.tzinfo is None:
        raise ValueError(f"La fecha limite de {entrega.uid} no lleva zona horaria.")
    local = entrega.due.astimezone(_TZ)
    dia = local.date()

    descripcion = DESCRIPCIONES.get(entrega.uid, entrega.title)
    resumen = f"Entrega {_etiqueta(entrega)} ({descripcion}) - {local:%H:%M}"

    lineas = [
        "BEGIN:VEVENT",
        f"UID:{_escapar(f'{entrega.uid}@{UID_DOMAIN}')}",
        f"DTSTAMP:{dtstamp}",
        f"DTSTART;VALUE=DATE:{dia:%Y%m%d}",
        f"DTEND;VALUE=DATE:{dia + timedelta(days=1):%Y%m%d}",
        f"SUMMARY:{_escapar(resumen)}",
    ]
    if entrega.url:
        lineas.append(f"DESCRIPTION:{_escapar(entrega.url)}")
    lineas.extend(["TRANSP:TRANSPARENT", "END:VEVENT"])
    return lineas


def _etiqueta(entrega: Assignment) -> str:
    """Siglas de la asignatura, con fallback hasta algo que nunca este vacio."""
    curso = entrega.course
    for candidata in (curso.acronym, ACRONIMOS.get(curso.code), curso.code, curso.name):
        if candidata and candidata.strip():
            return candidata.strip()
    return "?"


def _escapar(texto: str) -> str:
    """Escapado de TEXT segun RFC 5545 3.3.11. La barra invertida va primero."""
    texto = texto.replace("\r\n", "\n").replace("\r", "\n")
    return texto.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _plegar(linea: str) -> str:
    """Pliega una linea logica en fisicas de como mucho 75 octetos UTF-8.

    Las lineas de continuacion empiezan por un espacio, que cuenta dentro de los 75.
    Se corta siempre entre caracteres, nunca en mitad de una secuencia multibyte.
    """
    trozos: list[str] = []
    actual: list[str] = []
    octetos = 0
    limite = _MAX_OCTETOS
    for caracter in linea:
        tam = len(caracter.encode("utf-8"))
        if octetos + tam > limite:
            trozos.append("".join(actual))
            actual, octetos, limite = [], 0, _MAX_OCTETOS - 1
        actual.append(caracter)
        octetos += tam
    trozos.append("".join(actual))
    return (_CRLF + " ").join(trozos)
