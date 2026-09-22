"""Estado de los eventos del .ics de entregas, persistido en la cache SQLite.

Implementa `EventStateStore` (export_ics.py) sobre la tabla `ics_event_state`, que
crea la migracion 5 de cache/db.py, y guarda el hash de la ultima subida del feed
(`ics_feed_upload`, migracion 6) para que upv-publish no resuba lo mismo. Aqui solo
se guarda y se lee: cuando sube el SEQUENCE, cuando se retira una fila y cuando se
purga lo decide export_ics.py.

Fechas: texto ISO 8601 CON offset. Una fecha sin zona no se guarda ni se devuelve
nunca: se lanza ValueError en el acto. `datetime.fromisoformat` acepta texto sin
offset en silencio, y `astimezone` le pondria la zona del sistema sin avisar; los
dos caminos se cierran aqui.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType

from upv_mcp.cache.db import connect
from upv_mcp.export_ics import EventState


def _a_texto(instante: datetime, campo: str, *, utc: bool = False) -> str:
    # La comprobacion va ANTES de astimezone: sobre un naive, astimezone asume la
    # zona del sistema en silencio y el error desapareceria.
    if instante.tzinfo is None:
        raise ValueError(f"{campo}: no se guarda una fecha sin zona horaria ({instante}).")
    return (instante.astimezone(UTC) if utc else instante).isoformat()


def _de_texto(texto: str, campo: str) -> datetime:
    instante = datetime.fromisoformat(texto)
    if instante.tzinfo is None:
        raise ValueError(f"{campo}: la base tiene una fecha sin zona horaria ({texto!r}).")
    return instante


class SqliteEventStateStore:
    """`EventStateStore` sobre la cache SQLite del proyecto."""

    def __init__(self, db_path: Path) -> None:
        self._conn = connect(db_path)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> SqliteEventStateStore:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def get(self, uid: str) -> EventState | None:
        fila = self._conn.execute("SELECT * FROM ics_event_state WHERE uid = ?", (uid,)).fetchone()
        return None if fila is None else _estado(fila)

    def put(self, state: EventState) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO ics_event_state
                    (uid, due, summary, sequence, last_modified, retired_at)
                VALUES (?,?,?,?,?,?)
                """,
                (
                    state.uid,
                    _a_texto(state.due, "due"),
                    state.summary,
                    state.sequence,
                    _a_texto(state.last_modified, "last_modified", utc=True),
                    None
                    if state.retired_at is None
                    else _a_texto(state.retired_at, "retired_at", utc=True),
                ),
            )

    def all(self) -> list[EventState]:
        return [
            _estado(f) for f in self._conn.execute("SELECT * FROM ics_event_state ORDER BY uid")
        ]

    def delete(self, uid: str) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM ics_event_state WHERE uid = ?", (uid,))

    # -- Ultima subida (upv-publish) ------------------------------------------------

    def last_uploaded_sha256(self, feed: str) -> str | None:
        fila = self._conn.execute(
            "SELECT sha256 FROM ics_feed_upload WHERE feed = ?", (feed,)
        ).fetchone()
        return None if fila is None else str(fila["sha256"])

    def record_upload(self, feed: str, sha256: str, uploaded_at: datetime) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO ics_feed_upload (feed, sha256, uploaded_at) VALUES (?,?,?)",
                (feed, sha256, _a_texto(uploaded_at, "uploaded_at", utc=True)),
            )


def _estado(fila: sqlite3.Row) -> EventState:
    return EventState(
        uid=fila["uid"],
        due=_de_texto(fila["due"], "due"),
        summary=fila["summary"],
        sequence=int(fila["sequence"]),
        last_modified=_de_texto(fila["last_modified"], "last_modified"),
        retired_at=None
        if fila["retired_at"] is None
        else _de_texto(fila["retired_at"], "retired_at"),
    )
