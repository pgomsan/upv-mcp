"""Tests de las tools: filtrado, orden, truncado y honestidad de la cobertura."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from upv_mcp.cache.db import CacheRepository
from upv_mcp.config import Settings
from upv_mcp.models import EventKind
from upv_mcp.repository import AcademicRepository
from upv_mcp.sources.ics import IcsSource
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


def _repo_en(settings: Settings, cache: CacheRepository, momento: datetime) -> AcademicRepository:
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


async def test_next_class_trae_aula_y_docente(settings: Settings, cache: CacheRepository) -> None:
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


# -- pending_only ----------------------------------------------------------------


def _con_entregas(settings: Settings, cache: CacheRepository) -> AcademicRepository:
    """Cache con una entrega hecha, una sin hacer y una de estado desconocido."""
    from upv_mcp.models import (
        Assignment,
        Course,
        EventKind,
        SourceName,
        Submission,
        SubmissionStatus,
    )

    curso = Course(code="14541", name="Redes Industriales")
    base = datetime(2026, 3, 1, tzinfo=MADRID)
    cache.replace_calendar(
        "poliformat",
        [],
        [
            Assignment(
                uid="hecha",
                kind=EventKind.ASSIGNMENT,
                title="Hecha",
                course=curso,
                due=base,
                source=SourceName.POLIFORMAT,
                submission=Submission(status=SubmissionStatus.SUBMITTED, graded=False),
            ),
            Assignment(
                uid="falta",
                kind=EventKind.ASSIGNMENT,
                title="Falta",
                course=curso,
                due=base,
                source=SourceName.POLIFORMAT,
                submission=Submission(status=SubmissionStatus.NOT_SUBMITTED),
            ),
            Assignment(
                uid="ni-idea",
                kind=EventKind.ASSIGNMENT,
                title="Sin dato",
                course=curso,
                due=base,
                source=SourceName.POLIFORMAT,
                submission=Submission(status=SubmissionStatus.UNKNOWN),
            ),
            Assignment(
                uid="corregida",
                kind=EventKind.ASSIGNMENT,
                title="Corregida pero marcada como no entregada",
                course=curso,
                due=base,
                source=SourceName.POLIFORMAT,
                submission=Submission(
                    status=SubmissionStatus.NOT_SUBMITTED,
                    graded=True,
                    grade="9,50",
                    grade_max="10,00",
                ),
            ),
        ],
    )
    return _repo_en(settings, cache, datetime(2026, 2, 20, tzinfo=MADRID))


async def test_pending_only_filtra_lo_ya_entregado(
    settings: Settings, cache: CacheRepository
) -> None:
    repo = _con_entregas(settings, cache)
    resultado = await list_upcoming_deadlines(repo, 30, 0, True, limit=50)

    assert [d.uid for d in resultado.deadlines] == ["falta"]
    assert resultado.pending_only is True


async def test_pending_only_no_cuela_lo_desconocido(
    settings: Settings, cache: CacheRepository
) -> None:
    """Decir "te falta esto" de algo que quiza esta hecho es el peor fallo posible."""
    repo = _con_entregas(settings, cache)
    resultado = await list_upcoming_deadlines(repo, 30, 0, True, limit=50)

    assert "ni-idea" not in [d.uid for d in resultado.deadlines]


async def test_pending_only_no_cuela_lo_ya_corregido(
    settings: Settings, cache: CacheRepository
) -> None:
    """Con un 9,50 puesto, no queda nada que entregar, diga lo que diga el estado.

    PoliformaT marca NOT_SUBMITTED tareas ya corregidas cuando la entrega es de
    grupo. El invariante vive en el modelo para que ninguna fuente pueda reintroducir
    la contradiccion.
    """
    repo = _con_entregas(settings, cache)
    resultado = await list_upcoming_deadlines(repo, 30, 0, True, limit=50)

    assert "corregida" not in [d.uid for d in resultado.deadlines]


async def test_sin_filtro_devuelve_todas_y_avisa_de_las_desconocidas(
    settings: Settings, cache: CacheRepository
) -> None:
    repo = _con_entregas(settings, cache)
    resultado = await list_upcoming_deadlines(repo, 30, limit=50)

    assert len(resultado.deadlines) == 4
    assert "no traen estado de entrega" in (resultado.meta.coverage_note or "")


async def test_el_estado_sobrevive_a_la_cache(settings: Settings, cache: CacheRepository) -> None:
    """El estado se guarda y se recupera de SQLite sin perderse."""
    repo = _con_entregas(settings, cache)
    resultado = await list_upcoming_deadlines(repo, 30, limit=50)
    hecha = next(d for d in resultado.deadlines if d.uid == "hecha")

    assert hecha.submission is not None
    assert hecha.submission.status.value == "submitted"


# -- Filtro por asignatura -------------------------------------------------------


async def test_schedule_filtra_por_nombre_parcial(repo: AcademicRepository) -> None:
    """El usuario dice "Estadistica", no "14530"."""
    todo = await get_schedule(repo, date(2024, 1, 1), date(2026, 12, 31), limit=200)
    filtrado = await get_schedule(repo, date(2024, 1, 1), date(2026, 12, 31), "estad", limit=200)

    assert 0 < len(filtrado.sessions) < len(todo.sessions)
    assert all("Estad" in s.course.name for s in filtrado.sessions)


async def test_schedule_filtra_por_codigo_y_por_siglas(repo: AcademicRepository) -> None:
    por_codigo = await get_schedule(repo, date(2024, 1, 1), date(2026, 12, 31), "14530", limit=200)
    por_siglas = await get_schedule(repo, date(2024, 1, 1), date(2026, 12, 31), "EST", limit=200)

    assert por_codigo.sessions
    assert {s.uid for s in por_codigo.sessions} == {s.uid for s in por_siglas.sessions}


async def test_schedule_asignatura_inexistente_avisa(repo: AcademicRepository) -> None:
    """Sin aviso, un filtro que no encaja parece "no tienes clase ese dia"."""
    resultado = await get_schedule(
        repo, date(2024, 1, 1), date(2026, 12, 31), "Quimica Organica", limit=50
    )

    assert resultado.sessions == []
    nota = resultado.meta.coverage_note or ""
    assert "Ninguna asignatura tuya se llama" in nota
    assert "Estadística" in nota, "debe listar las asignaturas reales"


async def test_schedule_encuentra_asignatura_escrita_sin_tildes(
    repo: AcademicRepository,
) -> None:
    """Nadie teclea tildes en un chat, pero el .ics de la UPV las trae."""
    con_tilde = await get_schedule(
        repo, date(2024, 1, 1), date(2026, 12, 31), "Programación de Robots", limit=200
    )
    sin_tilde = await get_schedule(
        repo, date(2024, 1, 1), date(2026, 12, 31), "programacion de robots", limit=200
    )

    assert con_tilde.sessions, "el fixture debe traer esa asignatura"
    assert {s.uid for s in sin_tilde.sessions} == {s.uid for s in con_tilde.sessions}


async def test_schedule_no_niega_una_asignatura_real_sin_clases_en_el_rango(
    repo: AcademicRepository,
) -> None:
    """El fallo que mas dano hace: decirle que Estadistica no es suya.

    Vacio por el rango y vacio por el nombre se veian igual, y el aviso afirmaba lo
    segundo mientras listaba la asignatura entre las suyas, contradiciendose.
    """
    resultado = await get_schedule(
        repo, date(2026, 1, 1), date(2026, 1, 31), "Estadistica", limit=50
    )

    assert resultado.sessions == []
    nota = resultado.meta.coverage_note or ""
    assert "Estadística SI es una asignatura tuya" in nota
    assert "Ninguna asignatura tuya se llama" not in nota


async def test_deadlines_filtra_por_asignatura(
    tmp_path: Path, horario_ics: Path, examenes_ics: Path
) -> None:
    settings = Settings(
        schedule_ics_file=horario_ics, exams_ics_file=examenes_ics, data_dir=tmp_path / "d"
    )
    with CacheRepository(settings.db_path) as cache:
        repo = _repo_en(settings, cache, datetime(2026, 1, 1, tzinfo=MADRID))

        todas = await list_upcoming_deadlines(repo, 60, limit=50)
        solo = await list_upcoming_deadlines(repo, 60, 0, False, "Estadistica", limit=50)

        assert len(todas.deadlines) == 2
        assert len(solo.deadlines) == 1
        assert "Estad" in solo.deadlines[0].course.name


async def test_avisa_cuando_los_datos_no_llegan_a_la_fecha_preguntada(
    tmp_path: Path, horario_ics: Path, examenes_ics: Path
) -> None:
    """ "No tienes examenes" y "el calendario no llega hasta ahi" no son lo mismo.

    Caso real: en julio se pregunta por el proximo examen, el calendario cubre solo
    hasta junio del curso que acaba, y la respuesta vacia suena tranquilizadora
    cuando en realidad no se sabe nada de septiembre.
    """
    settings = Settings(
        schedule_ics_file=horario_ics, exams_ics_file=examenes_ics, data_dir=tmp_path / "d"
    )
    with CacheRepository(settings.db_path) as cache:
        # Los examenes del fixture son de enero de 2026; preguntamos desde julio.
        repo = _repo_en(settings, cache, datetime(2026, 7, 29, tzinfo=MADRID))
        resultado = await list_upcoming_deadlines(repo, 120, limit=50)

        assert resultado.deadlines == []
        nota = resultado.meta.coverage_note or ""
        assert "Los datos publicados llegan hasta" in nota
        assert "no significa que no haya nada" in nota


async def test_no_avisa_si_los_datos_cubren_la_pregunta(
    tmp_path: Path, horario_ics: Path, examenes_ics: Path
) -> None:
    """El aviso solo aparece cuando hace falta: si no, es ruido en cada respuesta."""
    settings = Settings(
        schedule_ics_file=horario_ics, exams_ics_file=examenes_ics, data_dir=tmp_path / "d"
    )
    with CacheRepository(settings.db_path) as cache:
        repo = _repo_en(settings, cache, datetime(2026, 1, 1, tzinfo=MADRID))
        resultado = await list_upcoming_deadlines(repo, 5, limit=50)

        assert "Los datos publicados llegan hasta" not in (resultado.meta.coverage_note or "")


async def test_schedule_avisa_si_el_rango_se_pasa_del_calendario_de_examenes(
    tmp_path: Path, horario_ics: Path, examenes_ics: Path
) -> None:
    """La forma mas creible de mentir que tiene esta tool.

    Caso real de la ronda con subagentes: se pregunta por un tramo de octubre, el
    horario si llega (las clases se publican por curso) pero el calendario de
    examenes acabo en junio. Devolver las clases y ningun examen, sin decir nada, se
    lee como "no tienes examenes en ese tramo" cuando lo cierto es que las fechas del
    curso siguiente aun no se han determinado.
    """
    settings = Settings(
        schedule_ics_file=horario_ics, exams_ics_file=examenes_ics, data_dir=tmp_path / "d"
    )
    with CacheRepository(settings.db_path) as cache:
        repo = _repo_en(settings, cache, datetime(2026, 7, 29, tzinfo=MADRID))
        # Los examenes del fixture son de enero de 2026; se pregunta mas alla.
        resultado = await get_schedule(repo, date(2026, 10, 1), date(2026, 10, 31), limit=50)

        nota = resultado.meta.coverage_note or ""
        assert "El calendario de EXAMENES acaba el" in nota
        assert "todavia no estan determinadas" in nota
        assert "no digas que no hay ninguno" in nota


def _clases_mas_alla_de_los_examenes(
    cache: CacheRepository, settings: Settings, horario_ics: Path, examenes_ics: Path
) -> None:
    """Deja la cache como estan los datos reales: clases que llegan mas lejos.

    En los fixtures pasa al reves (clases de 2024, examenes de 2026), y es el orden
    contrario el que produce el fallo que se quiere cubrir.
    """
    from upv_mcp.models import ClassSession, Course

    src = IcsSource(settings)
    clases = src.parse(horario_ics.read_text(encoding="utf-8"), "schedule")
    examenes = src.parse(examenes_ics.read_text(encoding="utf-8"), "exams")

    tardia = ClassSession(
        uid="clase-de-diciembre",
        kind=EventKind.CLASS,
        course=Course(code="14556", name="Modelado y Control de Robots"),
        start=datetime(2026, 12, 22, 15, 0, tzinfo=MADRID),
        end=datetime(2026, 12, 22, 17, 0, tzinfo=MADRID),
    )
    cache.replace_calendar("schedule", [*clases.sessions, tardia])
    cache.replace_calendar("exams", examenes.sessions, examenes.assignments)


async def test_el_aviso_de_examenes_dice_que_las_clases_SI_estan(
    tmp_path: Path, horario_ics: Path, examenes_ics: Path
) -> None:
    """Regresion de la ronda 2 con subagentes.

    El aviso anterior solo nombraba lo que faltaba, y el modelo lo comprimia de "el
    calendario de examenes" a "el calendario": acabo respondiendo "de julio en
    adelante no hay datos" cuando las clases estaban publicadas hasta diciembre.
    Decir en el mismo aviso hasta donde SI llegan las clases es lo que lo corta.
    """
    settings = Settings(
        schedule_ics_file=horario_ics, exams_ics_file=examenes_ics, data_dir=tmp_path / "d"
    )
    with CacheRepository(settings.db_path) as cache:
        _clases_mas_alla_de_los_examenes(cache, settings, horario_ics, examenes_ics)
        repo = _repo_en(settings, cache, datetime(2026, 7, 29, tzinfo=MADRID))

        resultado = await get_schedule(repo, date(2026, 10, 1), date(2026, 10, 31), limit=50)

        nota = resultado.meta.coverage_note or ""
        assert "Las CLASES si estan publicadas hasta el 2026-12-22" in nota
        assert "ni que faltan las clases" in nota
        assert "El horario de clases publicado acaba" not in nota, "octubre SI esta cubierto"


async def test_el_ultimo_dia_cubierto_no_dispara_el_aviso(
    tmp_path: Path, horario_ics: Path, examenes_ics: Path
) -> None:
    """Se compara por dia, no por instante.

    El rango llega a las 23:59 y el ultimo examen puede ser a las 11:00 del mismo
    dia: comparando instantes, preguntar justo por el mes que SI esta cubierto
    disparaba el aviso.
    """
    settings = Settings(
        schedule_ics_file=horario_ics, exams_ics_file=examenes_ics, data_dir=tmp_path / "d"
    )
    with CacheRepository(settings.db_path) as cache:
        _clases_mas_alla_de_los_examenes(cache, settings, horario_ics, examenes_ics)
        repo = _repo_en(settings, cache, datetime(2026, 7, 29, tzinfo=MADRID))
        ultimo = cache.latest_known_session(EventKind.EXAM)
        assert ultimo is not None

        resultado = await get_schedule(repo, ultimo.date(), ultimo.date(), limit=50)

        assert "El calendario de EXAMENES acaba el" not in (resultado.meta.coverage_note or "")


async def test_schedule_no_avisa_si_el_rango_cabe_en_los_dos_calendarios(
    tmp_path: Path, horario_ics: Path, examenes_ics: Path
) -> None:
    """El aviso solo cuando hace falta: en toda respuesta seria ruido, y se ignora."""
    settings = Settings(
        schedule_ics_file=horario_ics, exams_ics_file=examenes_ics, data_dir=tmp_path / "d"
    )
    with CacheRepository(settings.db_path) as cache:
        repo = _repo_en(settings, cache, datetime(2024, 9, 12, tzinfo=MADRID))
        resultado = await get_schedule(repo, date(2024, 9, 12), date(2024, 9, 12), limit=50)

        nota = resultado.meta.coverage_note or ""
        assert "acaba el" not in nota


async def test_schedule_avisa_del_horizonte_aunque_devuelva_clases(
    tmp_path: Path, horario_ics: Path, examenes_ics: Path
) -> None:
    """Una lista NO vacia es la que mas enganya: parece que la respuesta esta completa."""
    settings = Settings(
        schedule_ics_file=horario_ics, exams_ics_file=examenes_ics, data_dir=tmp_path / "d"
    )
    with CacheRepository(settings.db_path) as cache:
        repo = _repo_en(settings, cache, datetime(2026, 7, 29, tzinfo=MADRID))
        resultado = await get_schedule(repo, date(2024, 9, 12), date(2026, 12, 31), limit=200)

        assert resultado.sessions, "hay clases en el rango"
        assert "El calendario de EXAMENES acaba el" in (resultado.meta.coverage_note or "")
