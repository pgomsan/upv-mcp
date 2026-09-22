"""Tests de la cache SQLite: migraciones, consultas por rango y frescura."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from upv_mcp.cache.db import SCHEMA_VERSION, CacheRepository, connect
from upv_mcp.config import Settings
from upv_mcp.models import (
    Assignment,
    Course,
    EventKind,
    SourceName,
    Submission,
    SubmissionStatus,
)
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


def test_matching_course_names_ignora_tildes(
    cache: CacheRepository, horario_ics: Path, settings: Settings
) -> None:
    """SQLite no sabe de tildes: su LOWER() y su LIKE son ASCII.

    Por eso el encaje se hace en Python. "estadistica" tiene que encontrar
    "Estadística", que es como lo escribe el generador de la UPV.
    """
    payload = IcsSource(settings).parse(horario_ics.read_text(encoding="utf-8"), "schedule")
    cache.replace_calendar("schedule", payload.sessions)

    assert cache.matching_course_names("estadistica") == ["Estadística"]
    assert cache.matching_course_names("Estadística") == ["Estadística"]
    assert cache.matching_course_names("EST") == ["Estadística"], "por siglas"
    assert cache.matching_course_names("14530") == ["Estadística"], "por codigo"
    assert cache.matching_course_names("Quimica") == []


def test_catalogo_incluye_examenes_aunque_no_traigan_codigo(
    cache: CacheRepository, examenes_ics: Path, settings: Settings
) -> None:
    """Los examenes vienen del iCal sin `course_code`, asi que filtrar por codigo los
    dejaba fuera. Se filtra por nombre justamente para no perderlos."""
    payload = IcsSource(settings).parse(examenes_ics.read_text(encoding="utf-8"), "exams")
    cache.replace_calendar("exams", payload.sessions, payload.assignments)

    assert "Estadistica" in cache.matching_course_names("estadistica")

    inicio = datetime(2025, 1, 1, tzinfo=MADRID)
    fin = datetime(2027, 1, 1, tzinfo=MADRID)
    assert cache.count_assignments_between(inicio, fin, course="estadistica") == 1

    # Sin codigo, la misma asignatura entra dos veces en el catalogo; al usuario hay
    # que listarle nombres, no filas.
    assert len(cache.known_course_names()) == len(set(cache.known_course_names()))


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


def test_el_horizonte_de_entregas_acaba_en_la_fuente_mas_corta(
    cache: CacheRepository, examenes_ics: Path, settings: Settings
) -> None:
    """El horizonte honesto es el MINIMO por origen, no el MAXIMO de todo junto.

    Los examenes y las tareas de PoliformaT se publican por separado. Si se toma el
    maximo, unas tareas que lleguen a diciembre tapan que el calendario de examenes
    se acabo en enero, que es justo el hueco que el aviso debe delatar.
    """
    payload = IcsSource(settings).parse(examenes_ics.read_text(encoding="utf-8"), "exams")
    cache.replace_calendar("exams", payload.sessions, payload.assignments)

    solo_examenes = cache.latest_known_due()
    assert solo_examenes is not None

    tarea_tardia = Assignment(
        uid="tarea-diciembre",
        kind=EventKind.ASSIGNMENT,
        title="Memoria final",
        course=Course(code="14541", name="Redes Industriales"),
        due=datetime(2026, 12, 20, 23, 59, tzinfo=MADRID),
        source=SourceName.POLIFORMAT,
    )
    cache.replace_calendar("poliformat", [], [tarea_tardia])

    assert cache.latest_known_due() == solo_examenes, "la tarea de diciembre no lo estira"


def test_horizonte_por_tipo_de_sesion(
    cache: CacheRepository, horario_ics: Path, examenes_ics: Path, settings: Settings
) -> None:
    """`get_schedule` sirve dos calendarios y necesita saber donde acaba cada uno."""
    src = IcsSource(settings)
    clases = src.parse(horario_ics.read_text(encoding="utf-8"), "schedule")
    examenes = src.parse(examenes_ics.read_text(encoding="utf-8"), "exams")
    cache.replace_calendar("schedule", clases.sessions)
    cache.replace_calendar("exams", examenes.sessions, examenes.assignments)

    fin_clases = cache.latest_known_session(EventKind.CLASS)
    fin_examenes = cache.latest_known_session(EventKind.EXAM)

    assert fin_clases is not None and fin_examenes is not None
    assert fin_clases != fin_examenes, "son calendarios distintos, no el mismo dato"


def test_sin_examenes_el_horizonte_de_examenes_es_desconocido(
    cache: CacheRepository, horario_ics: Path, settings: Settings
) -> None:
    """Sin calendario de examenes no hay horizonte que anunciar: avisa el repositorio."""
    payload = IcsSource(settings).parse(horario_ics.read_text(encoding="utf-8"), "schedule")
    cache.replace_calendar("schedule", payload.sessions)

    assert cache.latest_known_session(EventKind.EXAM) is None


def test_migracion_5_crea_el_estado_del_feed_en_una_base_v4(tmp_path: Path) -> None:
    db = tmp_path / "v4.db"
    conn = connect(db)
    conn.execute("DROP TABLE ics_event_state")
    conn.execute("PRAGMA user_version = 4")
    conn.commit()
    conn.close()

    conn = connect(db)
    columnas = [f["name"] for f in conn.execute("PRAGMA table_info(ics_event_state)")]
    assert columnas == ["uid", "due", "summary", "sequence", "last_modified", "retired_at"]
    assert int(conn.execute("PRAGMA user_version").fetchone()[0]) == SCHEMA_VERSION
    conn.close()


def test_las_fechas_de_las_entregas_nunca_pierden_la_zona(
    cache: CacheRepository, tmp_path: Path, examenes_ics: Path, settings: Settings
) -> None:
    """Ida y vuelta por SQLite de todo datetime de una entrega.

    `_iso_utc` y `_from_iso` usan `astimezone`, que sobre un naive asume la zona
    del sistema SIN avisar. Por eso se comprueba en los tres puntos: lo que sale
    del parser, el texto en disco y lo que se lee.
    """
    payload = IcsSource(settings).parse(examenes_ics.read_text(encoding="utf-8"), "exams")
    assert payload.assignments
    for examen in payload.assignments:
        assert examen.due.tzinfo is not None, "el parser produjo una fecha sin zona"

    entrega = Assignment(
        uid="poliformat:assignment:x",
        kind=EventKind.ASSIGNMENT,
        title="Practica",
        course=Course(code="14534", name="ICD"),
        due=datetime(2026, 12, 1, 0, 30, tzinfo=MADRID),
        source=SourceName.POLIFORMAT,
        submission=Submission(
            status=SubmissionStatus.SUBMITTED,
            submitted_at=datetime(2026, 11, 30, 22, 0, tzinfo=MADRID),
        ),
    )
    cache.replace_calendar("exams", [], payload.assignments)
    cache.replace_calendar("poliformat", [], [entrega])

    crudo = sqlite3.connect(tmp_path / "cache.db")
    filas = crudo.execute("SELECT due_utc, submitted_at FROM assignments").fetchall()
    crudo.close()
    for due, submitted_at in filas:
        assert datetime.fromisoformat(due).tzinfo is not None, due
        if submitted_at is not None:
            assert datetime.fromisoformat(submitted_at).tzinfo is not None, submitted_at

    leidas = cache.assignments_between(
        datetime(2020, 1, 1, tzinfo=MADRID), datetime(2030, 1, 1, tzinfo=MADRID)
    )
    assert len(leidas) == len(filas)
    for leida in leidas:
        assert leida.due.tzinfo is not None
    leida = next(a for a in leidas if a.uid == entrega.uid)
    assert leida.due == entrega.due
    assert leida.submission is not None
    assert leida.submission.submitted_at == datetime(2026, 11, 30, 22, 0, tzinfo=MADRID)
