"""SEQUENCE y LAST-MODIFIED del .ics de entregas, y su persistencia en SQLite.

La logica (export_ics.py) se prueba con un store en memoria que cuenta escrituras;
el ciclo de vida completo se repite ademas contra SQLite (ics_state.py).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from upv_mcp.export_ics import (
    PURGA_RETIRADOS,
    EventState,
    EventStateStore,
    build_calendar,
)
from upv_mcp.ics_state import SqliteEventStateStore
from upv_mcp.models import Assignment, Course, EventKind, SourceName, Submission, SubmissionStatus

MADRID = ZoneInfo("Europe/Madrid")
T0 = datetime(2026, 9, 22, 11, 0, tzinfo=MADRID)
ICD = Course(code="14534", name="Infraestructura", acronym="ICD")


class MemoriaStore:
    """`EventStateStore` en un dict, contando escrituras."""

    def __init__(self) -> None:
        self.filas: dict[str, EventState] = {}
        self.escrituras = 0

    def get(self, uid: str) -> EventState | None:
        return self.filas.get(uid)

    def put(self, state: EventState) -> None:
        self.escrituras += 1
        self.filas[state.uid] = state

    def all(self) -> list[EventState]:
        return [self.filas[k] for k in sorted(self.filas)]

    def delete(self, uid: str) -> None:
        self.escrituras += 1
        del self.filas[uid]


def _entrega(
    clave: str,
    *,
    due: datetime | None = None,
    title: str = "Práctica",
    status: SubmissionStatus = SubmissionStatus.NOT_SUBMITTED,
) -> Assignment:
    return Assignment(
        uid=f"poliformat:assignment:{clave}",
        kind=EventKind.ASSIGNMENT,
        title=title,
        course=ICD,
        due=due or datetime(2026, 10, 19, 19, 9, tzinfo=MADRID),
        source=SourceName.POLIFORMAT,
        submission=Submission(status=status),
    )


def _uid(clave: str) -> str:
    return f"poliformat:assignment:{clave}@upv-mcp.local"


def _eventos(ics: str) -> dict[str, dict[str, str]]:
    """UID -> {propiedad: valor} de cada VEVENT, desplegado."""
    eventos: dict[str, dict[str, str]] = {}
    actual: dict[str, str] = {}
    for linea in ics.replace("\r\n ", "").split("\r\n"):
        if linea == "BEGIN:VEVENT":
            actual = {}
        elif linea == "END:VEVENT":
            eventos[actual["UID"]] = actual
        elif ":" in linea:
            nombre, valor = linea.split(":", 1)
            actual[nombre] = valor
    return eventos


def _utc(instante: datetime) -> str:
    return instante.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


@pytest.fixture(params=["memoria", "sqlite"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[EventStateStore]:
    if request.param == "memoria":
        yield MemoriaStore()
    else:
        with SqliteEventStateStore(tmp_path / "cache.db") as sqlite_store:
            yield sqlite_store


# -- Logica de SEQUENCE ---------------------------------------------------------------


def test_sin_store_todo_sale_con_sequence_0_y_last_modified_now() -> None:
    eventos = _eventos(build_calendar([_entrega("a"), _entrega("b")], now=T0))
    for evento in eventos.values():
        assert evento["SEQUENCE"] == "0"
        assert evento["LAST-MODIFIED"] == _utc(T0)


def test_primera_pasada_guarda_cada_evento_con_sequence_0() -> None:
    store = MemoriaStore()
    eventos = _eventos(build_calendar([_entrega("a"), _entrega("b")], now=T0, state=store))

    assert store.escrituras == 2
    assert {e["SEQUENCE"] for e in eventos.values()} == {"0"}
    assert store.filas[_uid("a")].last_modified == T0


def test_segunda_pasada_sin_cambios_no_incrementa_ni_escribe() -> None:
    store = MemoriaStore()
    entregas = [_entrega("a"), _entrega("b")]
    build_calendar(entregas, now=T0, state=store)
    store.escrituras = 0

    t1 = T0 + timedelta(hours=1)
    eventos = _eventos(build_calendar(entregas, now=t1, state=store))

    assert store.escrituras == 0
    for evento in eventos.values():
        assert evento["SEQUENCE"] == "0"
        assert evento["LAST-MODIFIED"] == _utc(T0)  # el de cuando cambio, no el de ahora
        assert evento["DTSTAMP"] == _utc(t1)


def test_cambiar_el_due_incrementa_solo_ese_evento() -> None:
    store = MemoriaStore()
    build_calendar([_entrega("a"), _entrega("b")], now=T0, state=store)
    store.escrituras = 0

    t1 = T0 + timedelta(days=1)
    nuevo_due = datetime(2026, 10, 26, 19, 9, tzinfo=MADRID)
    eventos = _eventos(
        build_calendar([_entrega("a", due=nuevo_due), _entrega("b")], now=t1, state=store)
    )

    assert store.escrituras == 1
    assert eventos[_uid("a")]["SEQUENCE"] == "1"
    assert eventos[_uid("a")]["LAST-MODIFIED"] == _utc(t1)
    assert eventos[_uid("b")]["SEQUENCE"] == "0"
    assert eventos[_uid("b")]["LAST-MODIFIED"] == _utc(T0)


def test_cambiar_el_titulo_tambien_incrementa() -> None:
    store = MemoriaStore()
    build_calendar([_entrega("a")], now=T0, state=store)
    eventos = _eventos(build_calendar([_entrega("a", title="Otra")], now=T0, state=store))
    assert eventos[_uid("a")]["SEQUENCE"] == "1"


def test_mismo_instante_en_otra_zona_no_es_un_cambio() -> None:
    store = MemoriaStore()
    due = datetime(2026, 10, 19, 19, 9, tzinfo=MADRID)
    build_calendar([_entrega("a", due=due)], now=T0, state=store)
    store.escrituras = 0
    build_calendar([_entrega("a", due=due.astimezone(UTC))], now=T0, state=store)
    assert store.escrituras == 0


def test_ciclo_completo(store: EventStateStore) -> None:
    t0, t1, t2, t3, t4 = (T0 + timedelta(days=d) for d in (0, 1, 2, 3, 4))
    a = _entrega("a")

    # Aparece.
    assert _eventos(build_calendar([a], now=t0, state=store))[_uid("a")]["SEQUENCE"] == "0"

    # Cambia.
    cambiada = _entrega("a", due=datetime(2026, 10, 26, 19, 9, tzinfo=MADRID))
    assert _eventos(build_calendar([cambiada], now=t1, state=store))[_uid("a")]["SEQUENCE"] == "1"

    # Desaparece: se entrega. La fila se queda, marcada como retirada.
    entregada = _entrega("a", status=SubmissionStatus.SUBMITTED)
    assert _eventos(build_calendar([entregada], now=t2, state=store)) == {}
    fila = store.get(_uid("a"))
    assert fila is not None
    assert fila.retired_at == t2
    assert fila.sequence == 1

    # Sigue sin aparecer: la fecha de retirada no se mueve.
    build_calendar([], now=t3, state=store)
    fila = store.get(_uid("a"))
    assert fila is not None
    assert fila.retired_at == t2

    # Vuelve (plazo reabierto), aunque sea identica a la ultima version publicada:
    # continua el SEQUENCE, nunca vuelve a 0.
    evento = _eventos(build_calendar([cambiada], now=t4, state=store))[_uid("a")]
    assert evento["SEQUENCE"] == "2"
    assert evento["LAST-MODIFIED"] == _utc(t4)
    fila = store.get(_uid("a"))
    assert fila is not None
    assert fila.retired_at is None
    assert fila.sequence == 2


def test_purga_a_los_180_dias(store: EventStateStore) -> None:
    # En UTC a proposito: T0 + 180 dias en hora de Madrid cruza el cambio de hora
    # de octubre y son 180 dias y 1 hora reales. La purga cuenta tiempo real.
    t0 = T0.astimezone(UTC)
    build_calendar([_entrega("a"), _entrega("b")], now=t0, state=store)
    build_calendar([_entrega("b")], now=t0, state=store)  # "a" se retira en t0

    build_calendar([_entrega("b")], now=t0 + PURGA_RETIRADOS, state=store)
    assert store.get(_uid("a")) is not None, "180 dias justos: todavia no"

    build_calendar([_entrega("b")], now=t0 + PURGA_RETIRADOS + timedelta(seconds=1), state=store)
    assert store.get(_uid("a")) is None
    assert store.get(_uid("b")) is not None, "lo publicado no se purga nunca"


def test_retirar_una_vez_no_reescribe_en_cada_pasada() -> None:
    store = MemoriaStore()
    build_calendar([_entrega("a")], now=T0, state=store)
    build_calendar([], now=T0 + timedelta(days=1), state=store)
    store.escrituras = 0
    build_calendar([], now=T0 + timedelta(days=2), state=store)
    assert store.escrituras == 0


def test_event_state_rechaza_fechas_sin_zona() -> None:
    with pytest.raises(ValueError):
        EventState("x", datetime(2026, 10, 19, 19, 9), "s", 0, T0)
    with pytest.raises(ValueError):
        EventState("x", T0, "s", 0, datetime(2026, 9, 22, 9, 0))
    with pytest.raises(ValueError):
        EventState("x", T0, "s", 0, T0, retired_at=datetime(2026, 9, 22, 9, 0))


# -- SQLite ---------------------------------------------------------------------------


def test_sqlite_ida_y_vuelta_nunca_pierde_la_zona(tmp_path: Path) -> None:
    db = tmp_path / "cache.db"
    estado = EventState(
        uid=_uid("a"),
        due=datetime(2026, 12, 1, 0, 30, tzinfo=MADRID),
        summary="Entrega ICD (Práctica\\, 1) - 00:30",
        sequence=3,
        last_modified=T0,
        retired_at=T0 + timedelta(days=1),
    )
    with SqliteEventStateStore(db) as store:
        store.put(estado)

    # En disco: texto ISO 8601 con offset, nunca sin el.
    conn = sqlite3.connect(db)
    due, last_modified, retired_at = conn.execute(
        "SELECT due, last_modified, retired_at FROM ics_event_state"
    ).fetchone()
    conn.close()
    assert due == "2026-12-01T00:30:00+01:00"
    assert last_modified == "2026-09-22T09:00:00+00:00"
    assert retired_at == "2026-09-23T09:00:00+00:00"

    # Reabriendo la base: mismos instantes, con zona.
    with SqliteEventStateStore(db) as store:
        leido = store.get(_uid("a"))
    assert leido is not None
    for campo in (leido.due, leido.last_modified, leido.retired_at):
        assert campo is not None
        assert campo.tzinfo is not None
    assert leido == estado


def test_sqlite_rechaza_leer_una_fecha_sin_zona(tmp_path: Path) -> None:
    db = tmp_path / "cache.db"
    with SqliteEventStateStore(db) as store:
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT INTO ics_event_state VALUES (?,?,?,?,?,NULL)",
            (_uid("a"), "2026-10-19T19:09:00", "s", 0, "2026-09-22T09:00:00+00:00"),
        )
        conn.commit()
        conn.close()
        with pytest.raises(ValueError, match="sin zona"):
            store.get(_uid("a"))


def test_sqlite_put_rechaza_fechas_sin_zona(tmp_path: Path) -> None:
    # EventState ya lo impide; esto cubre el caso de que alguien se lo salte.
    trampa = EventState(_uid("a"), T0, "s", 0, T0)
    object.__setattr__(trampa, "due", datetime(2026, 10, 19, 19, 9))
    with SqliteEventStateStore(tmp_path / "cache.db") as store, pytest.raises(ValueError):
        store.put(trampa)


def test_sqlite_all_y_delete(tmp_path: Path) -> None:
    with SqliteEventStateStore(tmp_path / "cache.db") as store:
        store.put(EventState(_uid("b"), T0, "s", 0, T0))
        store.put(EventState(_uid("a"), T0, "s", 0, T0))
        assert [e.uid for e in store.all()] == [_uid("a"), _uid("b")]
        store.delete(_uid("a"))
        assert [e.uid for e in store.all()] == [_uid("b")]
        assert store.get(_uid("a")) is None
