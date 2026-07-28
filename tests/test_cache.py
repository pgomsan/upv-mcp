"""Tests de la cache SQLite: migraciones, consultas por rango y frescura."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from upv_mcp.cache.db import SCHEMA_VERSION, CacheRepository, connect
from upv_mcp.config import Settings
from upv_mcp.models import EventKind
from upv_mcp.sources.ics import IcsSource

MADRID = ZoneInfo("Europe/Madrid")


def test_migracion_fija_user_version(tmp_path: Path) -> None:
    conn = connect(tmp_path / "c.db")
    assert int(conn.execute("PRAGMA user_version").fetchone()[0]) == SCHEMA_VERSION
    conn.close()


def test_migracion_es_idempotente(tmp_path: Path) -> None:
    """Reabrir la base no debe reaplicar el esquema ni perder datos."""
    db = tmp_path / "c.db"
    connect(db).close()
    conn = connect(db)
    assert int(conn.execute("PRAGMA user_version").fetchone()[0]) == SCHEMA_VERSION
    conn.close()


def test_guarda_y_recupera_sesiones(
    cache: CacheRepository, horario_ics: Path, settings: Settings
) -> None:
    payload = IcsSource(settings).parse(horario_ics.read_text(encoding="utf-8"), "schedule")
    cache.replace_calendar("schedule", payload.sessions, payload.assignments)

    recuperadas = cache.sessions_between(
        datetime(2024, 1, 1, tzinfo=MADRID), datetime(2026, 12, 31, tzinfo=MADRID)
    )
    assert len(recuperadas) == len(payload.sessions)
    original = payload.sessions[0]
    vuelta = recuperadas[0]
    assert vuelta.course == original.course
    assert vuelta.start == original.start
    assert vuelta.groups == original.groups
    assert vuelta.location == original.location


def test_reemplazo_total_borra_lo_que_desaparece(
    cache: CacheRepository, horario_ics: Path, settings: Settings
) -> None:
    """Si una clase se cae del horario, debe irse tambien de la cache."""
    payload = IcsSource(settings).parse(horario_ics.read_text(encoding="utf-8"), "schedule")
    cache.replace_calendar("schedule", payload.sessions)
    cache.replace_calendar("schedule", payload.sessions[:2])

    todas = cache.sessions_between(
        datetime(2024, 1, 1, tzinfo=MADRID), datetime(2026, 12, 31, tzinfo=MADRID)
    )
    assert len(todas) == 2


def test_rango_vacio_devuelve_lista_vacia(
    cache: CacheRepository, horario_ics: Path, settings: Settings
) -> None:
    payload = IcsSource(settings).parse(horario_ics.read_text(encoding="utf-8"), "schedule")
    cache.replace_calendar("schedule", payload.sessions)

    vacio = cache.sessions_between(
        datetime(2030, 1, 1, tzinfo=MADRID), datetime(2030, 1, 8, tzinfo=MADRID)
    )
    assert vacio == []


def test_consulta_por_rango_respeta_los_limites(
    cache: CacheRepository, horario_ics: Path, settings: Settings
) -> None:
    payload = IcsSource(settings).parse(horario_ics.read_text(encoding="utf-8"), "schedule")
    cache.replace_calendar("schedule", payload.sessions)

    inicio = datetime(2024, 9, 12, tzinfo=MADRID)
    fin = datetime(2024, 9, 13, tzinfo=MADRID)
    resultado = cache.sessions_between(inicio, fin)
    assert all(inicio <= s.start <= fin for s in resultado)


def test_limit_trunca_pero_el_contador_no(
    cache: CacheRepository, horario_ics: Path, settings: Settings
) -> None:
    """El truncado debe poder reportarse: contar y listar son operaciones distintas."""
    payload = IcsSource(settings).parse(horario_ics.read_text(encoding="utf-8"), "schedule")
    cache.replace_calendar("schedule", payload.sessions)

    inicio = datetime(2024, 1, 1, tzinfo=MADRID)
    fin = datetime(2026, 12, 31, tzinfo=MADRID)
    assert len(cache.sessions_between(inicio, fin, limit=3)) == 3
    assert cache.count_sessions_between(inicio, fin) == len(payload.sessions)


def test_next_session_after(cache: CacheRepository, horario_ics: Path, settings: Settings) -> None:
    payload = IcsSource(settings).parse(horario_ics.read_text(encoding="utf-8"), "schedule")
    cache.replace_calendar("schedule", payload.sessions)

    momento = datetime(2024, 9, 12, 0, 0, tzinfo=MADRID)
    siguiente = cache.next_session_after(momento)
    assert siguiente is not None
    assert siguiente.start > momento

    sin_nada = cache.next_session_after(datetime(2030, 1, 1, tzinfo=MADRID))
    assert sin_nada is None


def test_filtro_por_kind(cache: CacheRepository, examenes_ics: Path, settings: Settings) -> None:
    payload = IcsSource(settings).parse(examenes_ics.read_text(encoding="utf-8"), "exams")
    cache.replace_calendar("exams", payload.sessions, payload.assignments)

    inicio = datetime(2025, 1, 1, tzinfo=MADRID)
    fin = datetime(2027, 1, 1, tzinfo=MADRID)
    assert len(cache.sessions_between(inicio, fin, kinds=[EventKind.EXAM])) == 2
    assert cache.sessions_between(inicio, fin, kinds=[EventKind.CLASS]) == []
    assert cache.count_assignments_between(inicio, fin) == 2


def test_frescura_y_staleness(cache: CacheRepository) -> None:
    assert cache.is_empty()
    assert cache.is_stale(ttl_seconds=3600)

    viejo = datetime.now(UTC) - timedelta(hours=10)
    cache.replace_calendar("schedule", [], fetched_at=viejo)
    assert cache.is_stale(ttl_seconds=3600)

    cache.replace_calendar("schedule", [], fetched_at=datetime.now(UTC))
    assert not cache.is_stale(ttl_seconds=3600)


def test_calendarios_independientes(
    cache: CacheRepository, horario_ics: Path, examenes_ics: Path, settings: Settings
) -> None:
    """Refrescar el horario no debe borrar los examenes."""
    src = IcsSource(settings)
    clases = src.parse(horario_ics.read_text(encoding="utf-8"), "schedule")
    examenes = src.parse(examenes_ics.read_text(encoding="utf-8"), "exams")

    cache.replace_calendar("schedule", clases.sessions)
    cache.replace_calendar("exams", examenes.sessions, examenes.assignments)
    cache.replace_calendar("schedule", clases.sessions)

    inicio = datetime(2024, 1, 1, tzinfo=MADRID)
    fin = datetime(2027, 1, 1, tzinfo=MADRID)
    assert len(cache.sessions_between(inicio, fin, kinds=[EventKind.EXAM])) == 2


def test_una_migracion_invalida_la_cache(tmp_path: Path) -> None:
    """Regresion: tras migrar, la cache no puede seguir marcada como fresca.

    Una migracion anade datos que las descargas anteriores no guardaron. Si la
    frescura sobrevive, esos datos no se rellenan nunca y el usuario ve vacio sin
    ninguna explicacion. Paso de verdad con la tabla de asignaturas.
    """
    db = tmp_path / "vieja.db"
    conn = connect(db)
    conn.execute("PRAGMA user_version = 1")
    conn.execute(
        "INSERT INTO calendar_meta (calendar, fetched_at) VALUES ('schedule', ?)",
        (datetime.now(UTC).isoformat(),),
    )
    conn.commit()
    conn.close()

    with CacheRepository(db) as cache:
        assert cache.is_calendar_stale("schedule", ttl_seconds=3600)
        assert cache.oldest_fetched_at() is None
