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
    Announcement,
    Assignment,
    ClassSession,
    Course,
    CourseSite,
    EventKind,
    Location,
    Material,
    SourceName,
    Submission,
    SubmissionStatus,
)

#: Version de esquema que espera este codigo. Subirla obliga a anadir migracion.
SCHEMA_VERSION = 4

#: Migracion 4: asignaturas de PoliformaT y su sitio de Sakai. Permite pedir los
#: materiales de una sola asignatura en vez de los de todas.
_MIGRACION_4 = """
CREATE TABLE IF NOT EXISTS course_sites (
    course_code    TEXT PRIMARY KEY,
    site_id        TEXT NOT NULL,
    course_name    TEXT NOT NULL,
    course_acronym TEXT,
    calendar       TEXT NOT NULL
);
"""

#: Migracion 3: estado de entrega de cada tarea.
_MIGRACION_3 = """
ALTER TABLE assignments ADD COLUMN submission_status TEXT;
ALTER TABLE assignments ADD COLUMN submitted_at TEXT;
ALTER TABLE assignments ADD COLUMN submitted_late INTEGER;
ALTER TABLE assignments ADD COLUMN graded INTEGER;
ALTER TABLE assignments ADD COLUMN grade TEXT;
ALTER TABLE assignments ADD COLUMN grade_max TEXT;
ALTER TABLE assignments ADD COLUMN feedback TEXT;
"""

#: DDL de la migracion 2. El fichero schema.sql lo trae tambien, para bases nuevas;
#: aqui se repite lo minimo para poder aplicarlo sobre una base ya existente.
_MIGRACION_2 = """
CREATE TABLE IF NOT EXISTS materials (
    url            TEXT PRIMARY KEY,
    calendar       TEXT NOT NULL,
    course_code    TEXT NOT NULL,
    course_name    TEXT NOT NULL,
    course_acronym TEXT,
    title          TEXT NOT NULL,
    content_type   TEXT,
    updated_at     TEXT,
    size_bytes     INTEGER
);
CREATE INDEX IF NOT EXISTS idx_materials_course ON materials (course_code);
CREATE TABLE IF NOT EXISTS announcements (
    uid            TEXT PRIMARY KEY,
    calendar       TEXT NOT NULL,
    course_code    TEXT NOT NULL,
    course_name    TEXT NOT NULL,
    course_acronym TEXT,
    title          TEXT NOT NULL,
    body           TEXT NOT NULL,
    author         TEXT,
    published_at   TEXT NOT NULL,
    url            TEXT
);
CREATE INDEX IF NOT EXISTS idx_announcements_published ON announcements (published_at);
"""


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
        # Base nueva: schema.sql ya trae el esquema completo y actualizado.
        conn.executescript(_load_schema())
    else:
        # Base existente: se aplican solo las migraciones que le falten.
        if current < 2:
            conn.executescript(_MIGRACION_2)
        if current < 3:
            conn.executescript(_MIGRACION_3)
        if current < 4:
            conn.executescript(_MIGRACION_4)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


def _iso_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _bool_a_int(valor: bool | None) -> int | None:
    """SQLite no tiene booleanos, y None debe seguir siendo None (no 0)."""
    return None if valor is None else int(valor)


def _int_a_bool(valor: int | None) -> bool | None:
    return None if valor is None else bool(valor)


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
                    course_acronym, due_utc, room, building, location_raw, url, source,
                    submission_status, submitted_at, submitted_late, graded,
                    grade, grade_max, feedback
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                        a.submission.status.value if a.submission else None,
                        _iso_utc(a.submission.submitted_at)
                        if a.submission and a.submission.submitted_at
                        else None,
                        _bool_a_int(a.submission.late) if a.submission else None,
                        int(a.submission.graded) if a.submission else None,
                        a.submission.grade if a.submission else None,
                        a.submission.grade_max if a.submission else None,
                        a.submission.feedback if a.submission else None,
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

    def _course_clause(self, course: str | None) -> tuple[str, list[object]]:
        """Filtro por asignatura: por codigo exacto, o por texto en nombre o siglas.

        El usuario dice "Vision por Computador" o "VC", no "14537", asi que la
        busqueda por nombre es por subcadena y sin distinguir mayusculas.
        """
        if not course:
            return "", []
        termino = course.strip()
        if termino.isdigit():
            return " AND course_code = ?", [termino]
        patron = f"%{termino.lower()}%"
        return (
            " AND (LOWER(course_name) LIKE ? OR LOWER(COALESCE(course_acronym,'')) = ?)",
            [patron, termino.lower()],
        )

    def sessions_between(
        self,
        start: datetime,
        end: datetime,
        *,
        kinds: Iterable[EventKind] | None = None,
        course: str | None = None,
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
        clausula, extra = self._course_clause(course)
        sql += clausula
        params.extend(extra)
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
        course: str | None = None,
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
        clausula, extra = self._course_clause(course)
        sql += clausula
        params.extend(extra)
        return int(self._conn.execute(sql, params).fetchone()[0])

    def next_session_after(self, moment: datetime) -> ClassSession | None:
        row = self._conn.execute(
            "SELECT * FROM sessions WHERE start_utc > ? ORDER BY start_utc ASC LIMIT 1",
            (_iso_utc(moment),),
        ).fetchone()
        return self._row_to_session(row) if row else None

    def assignments_between(
        self,
        start: datetime,
        end: datetime,
        *,
        course: str | None = None,
        limit: int | None = None,
    ) -> list[Assignment]:
        sql = "SELECT * FROM assignments WHERE due_utc >= ? AND due_utc <= ?"
        params: list[object] = [_iso_utc(start), _iso_utc(end)]
        clausula, extra = self._course_clause(course)
        sql += clausula
        params.extend(extra)
        sql += " ORDER BY due_utc ASC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return [self._row_to_assignment(r) for r in self._conn.execute(sql, params)]

    def count_assignments_between(
        self, start: datetime, end: datetime, *, course: str | None = None
    ) -> int:
        sql = "SELECT COUNT(*) FROM assignments WHERE due_utc >= ? AND due_utc <= ?"
        params: list[object] = [_iso_utc(start), _iso_utc(end)]
        clausula, extra = self._course_clause(course)
        sql += clausula
        params.extend(extra)
        return int(self._conn.execute(sql, params).fetchone()[0])

    def replace_course_sites(self, calendar: str, sites: Sequence[CourseSite]) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM course_sites WHERE calendar = ?", (calendar,))
            self._conn.executemany(
                """
                INSERT OR REPLACE INTO course_sites (
                    course_code, site_id, course_name, course_acronym, calendar
                ) VALUES (?,?,?,?,?)
                """,
                [
                    (s.course.code, s.site_id, s.course.name, s.course.acronym, calendar)
                    for s in sites
                ],
            )

    def course_sites(self) -> list[CourseSite]:
        """Asignaturas de PoliformaT conocidas, ordenadas por nombre."""
        return [
            CourseSite(
                course=Course(
                    code=row["course_code"],
                    name=row["course_name"],
                    acronym=row["course_acronym"],
                ),
                site_id=row["site_id"],
            )
            for row in self._conn.execute("SELECT * FROM course_sites ORDER BY course_name ASC")
        ]

    def course_site(self, course_code: str) -> CourseSite | None:
        row = self._conn.execute(
            "SELECT * FROM course_sites WHERE course_code = ?", (course_code,)
        ).fetchone()
        if row is None:
            return None
        return CourseSite(
            course=Course(
                code=row["course_code"],
                name=row["course_name"],
                acronym=row["course_acronym"],
            ),
            site_id=row["site_id"],
        )

    def replace_materials(self, calendar: str, materials: Sequence[Material]) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM materials WHERE calendar = ?", (calendar,))
            self._conn.executemany(
                """
                INSERT OR REPLACE INTO materials (
                    url, calendar, course_code, course_name, course_acronym,
                    title, content_type, updated_at, size_bytes
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        m.url,
                        calendar,
                        m.course.code,
                        m.course.name,
                        m.course.acronym,
                        m.title,
                        m.content_type,
                        _iso_utc(m.updated_at) if m.updated_at else None,
                        m.size_bytes,
                    )
                    for m in materials
                ],
            )

    def replace_announcements(self, calendar: str, announcements: Sequence[Announcement]) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM announcements WHERE calendar = ?", (calendar,))
            self._conn.executemany(
                """
                INSERT OR REPLACE INTO announcements (
                    uid, calendar, course_code, course_name, course_acronym,
                    title, body, author, published_at, url
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        a.uid,
                        calendar,
                        a.course.code,
                        a.course.name,
                        a.course.acronym,
                        a.title,
                        a.body,
                        a.author,
                        _iso_utc(a.published_at),
                        a.url,
                    )
                    for a in announcements
                ],
            )

    def touch_calendar(self, calendar: str, fetched_at: datetime | None = None) -> None:
        """Marca un origen como consultado ahora, sin tocar sus datos."""
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO calendar_meta (calendar, fetched_at) VALUES (?,?)
                ON CONFLICT(calendar) DO UPDATE SET fetched_at = excluded.fetched_at
                """,
                (calendar, _iso_utc(fetched_at or datetime.now(UTC))),
            )

    def materials(self, course_code: str | None = None) -> list[Material]:
        """Materiales, opcionalmente de una sola asignatura."""
        sql = "SELECT * FROM materials"
        params: list[object] = []
        if course_code:
            sql += " WHERE course_code = ?"
            params.append(course_code)
        sql += " ORDER BY course_name ASC, title ASC"
        return [
            Material(
                course=_course(row),
                title=row["title"],
                url=row["url"],
                content_type=row["content_type"],
                updated_at=_from_iso(row["updated_at"], self._tz) if row["updated_at"] else None,
                size_bytes=row["size_bytes"],
            )
            for row in self._conn.execute(sql, params)
        ]

    def material_courses(self) -> list[Course]:
        """Asignaturas que tienen algun material, para el indice de resources."""
        filas = self._conn.execute(
            """
            SELECT course_code, course_name, course_acronym
            FROM materials GROUP BY course_code ORDER BY course_name
            """
        )
        return [
            Course(
                code=row["course_code"],
                name=row["course_name"],
                acronym=row["course_acronym"],
            )
            for row in filas
        ]

    def announcements(
        self, *, since: datetime | None = None, limit: int | None = None
    ) -> list[Announcement]:
        sql = "SELECT * FROM announcements"
        params: list[object] = []
        if since is not None:
            sql += " WHERE published_at >= ?"
            params.append(_iso_utc(since))
        sql += " ORDER BY published_at DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return [
            Announcement(
                uid=row["uid"],
                course=_course(row),
                title=row["title"],
                body=row["body"],
                author=row["author"],
                published_at=_from_iso(row["published_at"], self._tz),
                url=row["url"],
            )
            for row in self._conn.execute(sql, params)
        ]

    def count_announcements(self, *, since: datetime | None = None) -> int:
        sql = "SELECT COUNT(*) FROM announcements"
        params: list[object] = []
        if since is not None:
            sql += " WHERE published_at >= ?"
            params.append(_iso_utc(since))
        return int(self._conn.execute(sql, params).fetchone()[0])

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

    def is_calendar_stale(
        self, calendar: str, ttl_seconds: int, now: datetime | None = None
    ) -> bool:
        """Caducidad de UN origen concreto.

        Cada origen se refresca por su cuenta. Mirar solo la frescura global hacia
        que un origen nunca consultado quedara tapado por otro que si estaba al dia.
        Un calendario sin registro esta, por definicion, caducado.
        """
        stamp = self.fetched_at(calendar)
        if stamp is None:
            return True
        moment = now or datetime.now(self._tz)
        return (moment - stamp).total_seconds() > ttl_seconds

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
            submission=self._row_to_submission(row),
        )

    def _row_to_submission(self, row: sqlite3.Row) -> Submission | None:
        estado = row["submission_status"]
        if not estado:
            return None
        return Submission(
            status=SubmissionStatus(estado),
            submitted_at=(
                _from_iso(row["submitted_at"], self._tz) if row["submitted_at"] else None
            ),
            late=_int_a_bool(row["submitted_late"]),
            graded=bool(row["graded"]),
            grade=row["grade"],
            grade_max=row["grade_max"],
            feedback=row["feedback"],
        )
