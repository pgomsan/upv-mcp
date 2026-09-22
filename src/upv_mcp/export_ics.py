"""Exportacion de las entregas de PoliformaT a un calendario iCalendar (RFC 5545).

Pensado para suscribirse desde el movil: cada entrega pendiente aparece como un
evento de todo el dia en su fecha limite, con la hora real en el titulo.

Recibe los modelos ya normalizados y devuelve el texto. No toca red, cache ni
ficheros, y no sabe nada de MCP. Lo unico con memoria es el `EventStateStore` que
se le pase: la implementacion SQLite vive en ics_state.py, fuera de este modulo.

SEQUENCE y LAST-MODIFIED (RFC 5545 3.8.7): un cliente suscrito solo acepta la
nueva version de un evento si su SEQUENCE es MAYOR que la que ya tiene. Por eso el
estado de cada UID se recuerda entre pasadas y nunca se reinicia: ni cuando la
entrega deja de publicarse (se marca como retirada) ni cuando vuelve.

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
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Protocol
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

#: Cuanto se conserva la fila de un evento retirado antes de borrarla. Mientras
#: exista, si la entrega reaparece continua su SEQUENCE en vez de volver a 0.
PURGA_RETIRADOS = timedelta(days=180)

_TZ = ZoneInfo("Europe/Madrid")
_MAX_OCTETOS = 75
_CRLF = "\r\n"


@dataclass(frozen=True)
class EventState:
    """Lo ultimo que se publico de un evento, identificado por su UID completo."""

    uid: str
    due: datetime
    summary: str
    sequence: int
    last_modified: datetime
    retired_at: datetime | None = None

    def __post_init__(self) -> None:
        # Una fecha sin zona aqui acabaria en ValueError dentro de build_calendar,
        # de madrugada y en un log de launchd. Mejor reventar donde se crea.
        for campo in ("due", "last_modified", "retired_at"):
            valor = getattr(self, campo)
            if valor is not None and valor.tzinfo is None:
                raise ValueError(f"EventState.{campo} de {self.uid} no lleva zona horaria.")


class EventStateStore(Protocol):
    """Almacen del estado de los eventos. Solo guarda: la politica esta aqui."""

    def get(self, uid: str) -> EventState | None: ...
    def put(self, state: EventState) -> None: ...
    def all(self) -> Sequence[EventState]: ...
    def delete(self, uid: str) -> None: ...


def build_calendar(
    deadlines: Sequence[Assignment],
    *,
    now: datetime,
    state: EventStateStore | None = None,
) -> str:
    """Devuelve un VCALENDAR con un evento de todo el dia por entrega publicable.

    `now` es el DTSTAMP y, cuando algo cambia, el LAST-MODIFIED. Con el mismo `now`,
    los mismos datos y el mismo estado, la salida es identica byte a byte. Los
    eventos se ordenan por fecha limite y uid: no depende del orden de entrada.

    Sin `state`, todo sale con SEQUENCE 0 y LAST-MODIFIED = now, y no se guarda
    nada. Con `state`, cada evento conserva su SEQUENCE mientras no cambie, y los
    que dejan de publicarse se retiran (y se purgan a los `PURGA_RETIRADOS`).
    """
    if now.tzinfo is None:
        raise ValueError("now debe llevar zona horaria.")
    ahora = now.astimezone(UTC)
    dtstamp = _utc(ahora)

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
    publicados: set[str] = set()
    for entrega in publicables:
        uid = _uid(entrega)
        if uid in publicados:
            continue  # Dos eventos con el mismo UID en un feed confunden al cliente.
        publicados.add(uid)
        lineas.extend(_vevent(entrega, dtstamp, state, ahora))
    lineas.append("END:VCALENDAR")

    if state is not None:
        _retirar_y_purgar(state, publicados, ahora)

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


def _uid(entrega: Assignment) -> str:
    return f"{entrega.uid}@{UID_DOMAIN}"


def _utc(instante: datetime) -> str:
    return instante.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _vevent(
    entrega: Assignment, dtstamp: str, state: EventStateStore | None, ahora: datetime
) -> list[str]:
    if entrega.due.tzinfo is None:
        raise ValueError(f"La fecha limite de {entrega.uid} no lleva zona horaria.")
    local = entrega.due.astimezone(_TZ)
    dia = local.date()

    descripcion = DESCRIPCIONES.get(entrega.uid, entrega.title)
    resumen = f"Entrega {_etiqueta(entrega)} ({descripcion}) - {local:%H:%M}"
    uid = _uid(entrega)
    sequence, last_modified = _version(state, uid, entrega.due, resumen, ahora)

    lineas = [
        "BEGIN:VEVENT",
        f"UID:{_escapar(uid)}",
        f"DTSTAMP:{dtstamp}",
        f"LAST-MODIFIED:{_utc(last_modified)}",
        f"SEQUENCE:{sequence}",
        f"DTSTART;VALUE=DATE:{dia:%Y%m%d}",
        f"DTEND;VALUE=DATE:{dia + timedelta(days=1):%Y%m%d}",
        f"SUMMARY:{_escapar(resumen)}",
    ]
    if entrega.url:
        lineas.append(f"DESCRIPTION:{_escapar(entrega.url)}")
    lineas.extend(["TRANSP:TRANSPARENT", "END:VEVENT"])
    return lineas


def _version(
    state: EventStateStore | None, uid: str, due: datetime, summary: str, ahora: datetime
) -> tuple[int, datetime]:
    """SEQUENCE y LAST-MODIFIED de un evento, guardando el estado si cambia.

    Solo se escribe cuando hay algo nuevo: una pasada sin cambios no toca el store.
    Un evento retirado que reaparece continua su SEQUENCE; nunca vuelve a 0, porque
    un cliente que aun lo tenga cacheado ignoraria la version "vieja".
    """
    if state is None:
        return 0, ahora
    previo = state.get(uid)
    if previo is None:
        sequence = 0
    elif previo.retired_at is None and previo.due == due and previo.summary == summary:
        return previo.sequence, previo.last_modified
    else:
        sequence = previo.sequence + 1
    state.put(EventState(uid, due, summary, sequence, ahora))
    return sequence, ahora


def _retirar_y_purgar(state: EventStateStore, publicados: set[str], ahora: datetime) -> None:
    """Marca como retirado lo que ya no se publica y purga lo retirado hace mucho.

    La fila no se borra al retirar: si la entrega reaparece (p.ej. el profesor
    reabre el plazo), su SEQUENCE tiene que continuar donde lo dejo.
    """
    for estado in state.all():
        if estado.uid in publicados:
            continue
        if estado.retired_at is None:
            state.put(replace(estado, retired_at=ahora))
        elif ahora - estado.retired_at > PURGA_RETIRADOS:
            state.delete(estado.uid)


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
