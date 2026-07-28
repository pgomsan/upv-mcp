"""Cache SQLite: conexion, migraciones versionadas y acceso a datos.

sqlite3 de la stdlib, sin ORM. La cache existe por dos razones: evitar bajar 400 KB
de .ics en cada llamada a una tool, y poder responder cuando no hay red.

INVARIANTE: esta capa habla de modelos de dominio, no de MCP.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from types import TracebackType
from zoneinfo import ZoneInfo

from upv_mcp.models import (
    Assignment,
    ClassSession,
    Course,
    EventKind,
    Location,
    SourceName,
)

#: Version de esquema que espera este codigo. Subirla obliga a anadir migracion.
SCHEMA_VERSION = 1


def _load_schema() -> str:
    return resources.files("upv_mcp.cache").joinpath("schema.sql").read_text(encoding="utf-8")


def connect(db_path: Path) -> sqlite3.Connection:
    """Abre la base y aplica las migraciones pendientes."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    """Aplica migraciones incrementales segun PRAGMA user_version.

    Con una sola version el esquema entero es la migracion 1. Al anadir la 2 se
    escribe su DDL aqui y se sube SCHEMA_VERSION.
    """
    current = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if current >= SCHEMA_VERSION:
        return
    if current < 1:
        conn.executescript(_load_schema())
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


def _iso_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _from_iso(value: str, tz: ZoneInfo) -> datetime:
    return datetime.fromisoformat(value).astimezone(tz)


def _location(row: sqlite3.Row) -> Location | None:
    if row["location_raw"] is None and row["room"] is None:
        return None
    return Location(
        room=row["room"],
        building=row["building"],
        raw=row["location_raw"] or "",
    )


def _course(row: sqlite3.Row) -> Course:
    return Course(
        code=row["course_code"],
        name=row["course_name"],
        acronym=row["course_acronym"],
    )


class CacheRepository:
    """Acceso a la cache. Es sincrono a proposito: sqlite3 lo es."""

    def __init__(self, db_path: Path, timezone: str = "Europe/Madrid") -> None:
        self._conn = connect(db_path)
        self._tz = ZoneInfo(timezone)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> CacheRepository:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # -- Escritura ----------------------------------------------------------------

    def replace_calendar(
        self,
        calendar: str,
        sessions: Sequence[ClassSession],
        assignments: Sequence[Assignment] = (),
        *,
        fetched_at: datetime | None = None,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> None:
        """Sustituye por completo el contenido de un calendario.

        Reemplazo total y no merge: si una clase desaparece del horario (cambio de
        aula, asignatura dada de baja), debe desaparecer tambien de la cache.
        """
        stamp = fetched_at or datetime.now(UTC)
        with self._conn:
            self._conn.execute("DELETE FROM sessions WHERE calendar = ?", (calendar,))
            self._conn.execute("DELETE FROM assignments WHERE calendar = ?", (calendar,))
            self._conn.executemany(
                """
                INSERT OR REPLACE INTO sessions (
                    uid, calendar, kind, course_code, course_name, course_acronym,
                    start_utc, end_utc, room, building, location_raw, teacher,
                    teaching_type, groups_json, source
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        s.uid,
                        calendar,
                        s.kind.value,
                        s.course.code,
                        s.course.name,
                        s.course.acronym,
                        _iso_utc(s.start),
                        _iso_utc(s.end),
                        s.location.room if s.location else None,
                        s.location.building if s.location else None,
                        s.location.raw if s.location else None,
                        s.teacher,
                        s.teaching_type,
                        json.dumps(s.groups, ensure_ascii=False),
                        s.source.value,
                    )
                    for s in sessions
                ],
            )
            self._conn.executemany(
                """
                INSERT OR REPLACE INTO assignments (
                    uid, calendar, kind, title, course_code, course_name,
                    course_acronym, due_utc, room, building, location_raw, url, source
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        a.uid,
                        calendar,
                        a.kind.value,
                        a.title,
                        a.course.code,
                        a.course.name,
                        a.course.acronym,
                        _iso_utc(a.due),
                        a.location.room if a.location else None,
                        a.location.building if a.location else None,
                        a.location.raw if a.location else None,
                        a.url,
                        a.source.value,
                    )
                    for a in assignments
                ],
            )
            self._conn.execute(
                """
                INSERT INTO calendar_meta (calendar, fetched_at, etag, last_modified)
                VALUES (?,?,?,?)
                ON CONFLICT(calendar) DO UPDATE SET
                    fetched_at = excluded.fetched_at,
                    etag = excluded.etag,
                    last_modified = excluded.last_modified
                """,
                (calendar, _iso_utc(stamp), etag, last_modified),
            )

    # -- Lectura ------------------------------------------------------------------

    def sessions_between(
        self,
        start: datetime,
        end: datetime,
        *,
        kinds: Iterable[EventKind] | None = None,
        limit: int | None = None,
    ) -> list[ClassSession]:
        """Sesiones que empiezan dentro de [start, end], ordenadas cronologicamente.

        El filtrado se hace en SQL y no en Python: no tiene sentido materializar 841
        eventos para devolver 6.
        """
        sql = "SELECT * FROM sessions WHERE start_utc >= ? AND start_utc <= ?"
        params: list[object] = [_iso_utc(start), _iso_utc(end)]
        if kinds is not None:
            values = [k.value for k in kinds]
            if not values:
                return []
            sql += f" AND kind IN ({','.join('?' * len(values))})"
            params.extend(values)
        sql += " ORDER BY start_utc ASC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return [self._row_to_session(r) for r in self._conn.execute(sql, params)]

    def count_sessions_between(
        self,
        start: datetime,
        end: datetime,
        *,
        kinds: Iterable[EventKind] | None = None,
    ) -> int:
        """Cuenta sin materializar, para poder informar de truncado con honestidad."""
        sql = "SELECT COUNT(*) FROM sessions WHERE start_utc >= ? AND start_utc <= ?"
        params: list[object] = [_iso_utc(start), _iso_utc(end)]
        if kinds is not None:
            values = [k.value for k in kinds]
            if not values:
                return 0
            sql += f" AND kind IN ({','.join('?' * len(values))})"
            params.extend(values)
        return int(self._conn.execute(sql, params).fetchone()[0])

    def next_session_after(self, moment: datetime) -> ClassSession | None:
        row = self._conn.execute(
            "SELECT * FROM sessions WHERE start_utc > ? ORDER BY start_utc ASC LIMIT 1",
            (_iso_utc(moment),),
        ).fetchone()
        return self._row_to_session(row) if row else None

    def assignments_between(
        self, start: datetime, end: datetime, *, limit: int | None = None
    ) -> list[Assignment]:
        sql = "SELECT * FROM assignments WHERE due_utc >= ? AND due_utc <= ? ORDER BY due_utc ASC"
        params: list[object] = [_iso_utc(start), _iso_utc(end)]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return [self._row_to_assignment(r) for r in self._conn.execute(sql, params)]

    def count_assignments_between(self, start: datetime, end: datetime) -> int:
        return int(
            self._conn.execute(
                "SELECT COUNT(*) FROM assignments WHERE due_utc >= ? AND due_utc <= ?",
                (_iso_utc(start), _iso_utc(end)),
            ).fetchone()[0]
        )

    def courses_by_code(self) -> dict[str, Course]:
        """Asignaturas conocidas por el horario, indexadas por codigo UPV.

        Permite dar a las entregas de PoliformaT el nombre oficial de la asignatura
        en vez del titulo del sitio de Sakai ("PR3 25.26").
        """
        filas = self._conn.execute(
            """
            SELECT course_code, course_name, course_acronym
            FROM sessions
            WHERE course_code <> ''
            GROUP BY course_code
            """
        )
        return {
            row["course_code"]: Course(
                code=row["course_code"],
                name=row["course_name"],
                acronym=row["course_acronym"],
            )
            for row in filas
        }

    def is_empty(self) -> bool:
        return int(self._conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]) == 0

    def fetched_at(self, calendar: str) -> datetime | None:
        row = self._conn.execute(
            "SELECT fetched_at FROM calendar_meta WHERE calendar = ?", (calendar,)
        ).fetchone()
        return _from_iso(row["fetched_at"], self._tz) if row else None

    def oldest_fetched_at(self) -> datetime | None:
        """La frescura de la cache es la del calendario mas atrasado."""
        row = self._conn.execute("SELECT MIN(fetched_at) AS f FROM calendar_meta").fetchone()
        return _from_iso(row["f"], self._tz) if row and row["f"] else None

    def is_stale(self, ttl_seconds: int, now: datetime | None = None) -> bool:
        oldest = self.oldest_fetched_at()
        if oldest is None:
            return True
        moment = now or datetime.now(self._tz)
        return (moment - oldest).total_seconds() > ttl_seconds

    # -- Mapeo --------------------------------------------------------------------

    def _row_to_session(self, row: sqlite3.Row) -> ClassSession:
        return ClassSession(
            uid=row["uid"],
            kind=EventKind(row["kind"]),
            course=_course(row),
            start=_from_iso(row["start_utc"], self._tz),
            end=_from_iso(row["end_utc"], self._tz),
            location=_location(row),
            teacher=row["teacher"],
            teaching_type=row["teaching_type"],
            groups=list(json.loads(row["groups_json"])),
            source=SourceName(row["source"]),
        )

    def _row_to_assignment(self, row: sqlite3.Row) -> Assignment:
        return Assignment(
            uid=row["uid"],
            kind=EventKind(row["kind"]),
            title=row["title"],
            course=_course(row),
            due=_from_iso(row["due_utc"], self._tz),
            location=_location(row),
            url=row["url"],
            source=SourceName(row["source"]),
        )
