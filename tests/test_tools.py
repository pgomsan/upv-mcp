"""Tests de las tools: filtrado, orden, truncado y honestidad de la cobertura."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from upv_mcp.cache.db import CacheRepository
from upv_mcp.config import Settings
from upv_mcp.repository import AcademicRepository
from upv_mcp.tools.deadlines import list_upcoming_deadlines
from upv_mcp.tools.next_class import get_next_class
from upv_mcp.tools.schedule import get_schedule

MADRID = ZoneInfo("Europe/Madrid")


class _RepoEnFecha(AcademicRepository):
    """Repositorio con un 'ahora' fijo, para poder testear lo relativo a la hora."""

    def __init__(self, *args: object, momento: datetime, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._momento = momento

    def now(self) -> datetime:
        return self._momento


@pytest.fixture
def repo(settings: Settings, cache: CacheRepository) -> AcademicRepository:
    return AcademicRepository(settings, cache)


def _repo_en(
    settings: Settings, cache: CacheRepository, momento: datetime
) -> AcademicRepository:
    return _RepoEnFecha(settings, cache, momento=momento)


# -- get_schedule ----------------------------------------------------------------


async def test_schedule_devuelve_solo_lo_del_rango(repo: AcademicRepository) -> None:
    resultado = await get_schedule(repo, date(2024, 9, 12), date(2024, 9, 12), limit=50)

    assert resultado.sessions
    for s in resultado.sessions:
        assert s.start.date() == date(2024, 9, 12)


async def test_schedule_incluye_el_dia_final_entero(repo: AcademicRepository) -> None:
    """Un rango de un solo dia no puede devolver vacio por culpa de las horas."""
    un_dia = await get_schedule(repo, date(2024, 9, 12), date(2024, 9, 12), limit=50)
    assert len(un_dia.sessions) > 0
    assert un_dia.range_end.hour == 23


async def test_schedule_rango_vacio(repo: AcademicRepository) -> None:
    """Vacio es una respuesta legitima, no un error."""
    resultado = await get_schedule(repo, date(2030, 1, 1), date(2030, 1, 7), limit=50)

    assert resultado.sessions == []
    assert resultado.meta.total_matching == 0
    assert resultado.meta.truncated is False


async def test_schedule_ordenado_cronologicamente(repo: AcademicRepository) -> None:
    resultado = await get_schedule(repo, date(2024, 1, 1), date(2026, 12, 31), limit=50)
    starts = [s.start for s in resultado.sessions]
    assert starts == sorted(starts)


async def test_schedule_trunca_y_lo_dice(repo: AcademicRepository) -> None:
    """Nunca inundar el contexto: recortar y avisar."""
    resultado = await get_schedule(repo, date(2024, 1, 1), date(2026, 12, 31), limit=3)

    assert len(resultado.sessions) == 3
    assert resultado.meta.returned == 3
    assert resultado.meta.total_matching > 3
    assert resultado.meta.truncated is True


async def test_schedule_rechaza_rango_invertido(repo: AcademicRepository) -> None:
    with pytest.raises(ValueError, match="anterior a start_date"):
        await get_schedule(repo, date(2024, 9, 12), date(2024, 9, 1), limit=50)


# -- get_next_class --------------------------------------------------------------


async def test_next_class_es_la_siguiente_no_la_anterior(
    settings: Settings, cache: CacheRepository
) -> None:
    momento = datetime(2024, 9, 12, 8, 0, tzinfo=MADRID)
    resultado = await get_next_class(_repo_en(settings, cache, momento))

    assert resultado.session is not None
    assert resultado.session.start > momento
    assert resultado.starts_in_minutes is not None
    assert resultado.starts_in_minutes > 0


async def test_next_class_calcula_los_minutos_que_faltan(
    settings: Settings, cache: CacheRepository
) -> None:
    momento = datetime(2024, 9, 12, 14, 0, tzinfo=MADRID)  # la clase es a las 15:00
    resultado = await get_next_class(_repo_en(settings, cache, momento))

    assert resultado.session is not None
    esperado = int((resultado.session.start - momento).total_seconds() // 60)
    assert resultado.starts_in_minutes == esperado


async def test_next_class_sin_clases_futuras_devuelve_null(
    settings: Settings, cache: CacheRepository
) -> None:
    """Fin de curso: hay que decir que no hay, no inventar una clase."""
    momento = datetime(2030, 1, 1, tzinfo=MADRID)
    resultado = await get_next_class(_repo_en(settings, cache, momento))

    assert resultado.session is None
    assert resultado.starts_in_minutes is None
    assert "No queda ninguna clase" in (resultado.meta.coverage_note or "")


async def test_next_class_trae_aula_y_docente(
    settings: Settings, cache: CacheRepository
) -> None:
    """La pregunta tipica es 'donde tengo que ir': el aula es obligatoria."""
    momento = datetime(2024, 9, 12, 8, 0, tzinfo=MADRID)
    resultado = await get_next_class(_repo_en(settings, cache, momento))

    assert resultado.session is not None
    assert resultado.session.location is not None
    assert resultado.session.location.room
    assert resultado.session.course.name


# -- list_upcoming_deadlines -----------------------------------------------------


async def test_deadlines_vacio_pero_avisando(repo: AcademicRepository) -> None:
    """El caso real de la v0: sin calendario de examenes no hay nada que devolver.

    Lo importante es que la respuesta explique POR QUE esta vacia, para que el
    modelo no concluya que el estudiante no tiene examenes.
    """
    resultado = await list_upcoming_deadlines(repo, 30, limit=50)

    assert resultado.deadlines == []
    nota = resultado.meta.coverage_note or ""
    assert "NO tienes acceso al calendario de examenes" in nota
    assert "PoliformaT" in nota


async def test_deadlines_devuelve_examenes_si_hay_calendario(
    tmp_path: Path, horario_ics: Path, examenes_ics: Path
) -> None:
    """Con el segundo feed conectado, la tool funciona sin tocar su codigo."""
    settings = Settings(
        schedule_ics_file=horario_ics,
        exams_ics_file=examenes_ics,
        data_dir=tmp_path / "d",
    )
    with CacheRepository(settings.db_path) as cache:
        repo = _repo_en(settings, cache, datetime(2026, 1, 1, tzinfo=MADRID))
        resultado = await list_upcoming_deadlines(repo, 60, limit=50)

        assert len(resultado.deadlines) == 2
        assert resultado.deadlines[0].due < resultado.deadlines[1].due
        assert "NO tienes acceso al calendario de examenes" not in (
            resultado.meta.coverage_note or ""
        )


async def test_deadlines_valida_el_horizonte(repo: AcademicRepository) -> None:
    with pytest.raises(ValueError, match="entre 1 y 365"):
        await list_upcoming_deadlines(repo, 0, limit=50)
    with pytest.raises(ValueError, match="entre 1 y 365"):
        await list_upcoming_deadlines(repo, 400, limit=50)


async def test_deadlines_no_devuelve_clases(
    tmp_path: Path, horario_ics: Path, examenes_ics: Path
) -> None:
    """Una clase no es una fecha limite, por mucho que este en el calendario."""
    settings = Settings(
        schedule_ics_file=horario_ics,
        exams_ics_file=examenes_ics,
        data_dir=tmp_path / "d",
    )
    with CacheRepository(settings.db_path) as cache:
        repo = _repo_en(settings, cache, datetime(2024, 9, 1, tzinfo=MADRID))
        resultado = await list_upcoming_deadlines(repo, 365, limit=50)

        assert all(d.kind == "exam" for d in resultado.deadlines)


# -- transversal -----------------------------------------------------------------


async def test_todas_las_respuestas_llevan_hora_del_servidor(
    repo: AcademicRepository,
) -> None:
    """El modelo necesita saber que dia es hoy para resolver fechas relativas."""
    momento_antes = repo.now() - timedelta(seconds=5)

    schedule = await get_schedule(repo, date(2024, 9, 12), date(2024, 9, 12), limit=50)
    siguiente = await get_next_class(repo)
    limites = await list_upcoming_deadlines(repo, 14, limit=50)

    for resultado in (schedule, siguiente, limites):
        assert resultado.meta.generated_at > momento_antes
        assert resultado.meta.generated_at.tzinfo is not None


# -- days_back (mirar atras) -----------------------------------------------------


async def test_deadlines_por_defecto_no_mira_atras(
    tmp_path: Path, horario_ics: Path, examenes_ics: Path
) -> None:
    """Lo normal es preguntar por lo que queda, no por lo ya entregado."""
    settings = Settings(
        schedule_ics_file=horario_ics, exams_ics_file=examenes_ics, data_dir=tmp_path / "d"
    )
    with CacheRepository(settings.db_path) as cache:
        # Los examenes del fixture son de enero de 2026; nos situamos despues.
        repo = _repo_en(settings, cache, datetime(2026, 3, 1, tzinfo=MADRID))

        sin_pasado = await list_upcoming_deadlines(repo, 30, limit=50)
        assert sin_pasado.deadlines == []
        assert sin_pasado.days_back == 0


async def test_deadlines_con_days_back_recupera_lo_pasado(
    tmp_path: Path, horario_ics: Path, examenes_ics: Path
) -> None:
    """Para 'que entregue en Vision por Computador' hay que poder mirar atras."""
    settings = Settings(
        schedule_ics_file=horario_ics, exams_ics_file=examenes_ics, data_dir=tmp_path / "d"
    )
    with CacheRepository(settings.db_path) as cache:
        repo = _repo_en(settings, cache, datetime(2026, 3, 1, tzinfo=MADRID))

        con_pasado = await list_upcoming_deadlines(repo, 30, 90, limit=50)
        assert len(con_pasado.deadlines) == 2
        assert con_pasado.days_back == 90
        assert all(d.due < repo.now() for d in con_pasado.deadlines)


async def test_deadlines_valida_days_back(repo: AcademicRepository) -> None:
    with pytest.raises(ValueError, match="days_back debe estar entre 0 y 365"):
        await list_upcoming_deadlines(repo, 14, -1, limit=50)
    with pytest.raises(ValueError, match="days_back debe estar entre 0 y 365"):
        await list_upcoming_deadlines(repo, 14, 400, limit=50)
