"""Tests del origen PoliformaT.

Las respuestas simuladas reproducen la forma REAL de la API, verificada contra
poliformat.upv.es: dos formatos de fecha distintos, HTML con entidades en los
titulos, y eventos de calendario que duplican tareas.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from upv_mcp.config import Settings
from upv_mcp.models import EventKind, SourceName, SubmissionStatus
from upv_mcp.sources.poliformat import PoliformatSource, parse_site_id

MADRID = ZoneInfo("Europe/Madrid")

# 2026-04-15T11:00:00Z -> 13:00 en Valencia (CEST)
_TAREAS: dict[str, Any] = {
    "assignment_collection": [
        {
            "id": "aaa-111",
            "title": "Pr&aacute;ctica01-PL-3A1",
            "context": "GRA_14541_2025",
            "dueTimeString": "2026-04-15T11:00:00Z",
            "dueTime": {"epochSecond": 1776250800, "nano": 0},
            "status": "CLOSED",
            "entityURL": "https://poliformat.upv.es/direct/assignment/aaa-111",
        },
        {
            "id": "bbb-222",
            "title": "Entrega de un curso viejo",
            "context": "GRA_14530_2023",
            "dueTimeString": "2024-03-01T10:00:00Z",
        },
        {
            "id": "ccc-333",
            "title": "Tarea sin fecha",
            "context": "GRA_14541_2025",
            "dueTimeString": None,
        },
    ]
}

_CALENDARIO: dict[str, Any] = {
    "calendar_collection": [
        {
            # Duplicado de la tarea aaa-111: trae assignmentId.
            "eventId": "evt-dup",
            "assignmentId": "aaca3233",
            "type": "Deadline",
            "title": "Entrega Pr&aacute;ctica01-PL-3A1",
            "siteId": "GRA_14541_2025",
            "firstTime": {"display": "15 abr 2026 13:00", "time": 1776250800000},
        },
        {
            # Fecha limite propia del profesor, sin tarea asociada.
            "eventId": "evt-propio",
            "type": "Deadline",
            "title": "Entrega memoria final",
            "siteId": "GRA_14541_2025",
            "firstTime": {"display": "22 jun 2026 19:09", "time": 1782148140000},
        },
        {
            "eventId": "evt-actividad",
            "type": "Activity",
            "title": "Inicio de las clases",
            "siteId": "GRA_14541_2025",
            "firstTime": {"display": "1 sep 2025 08:00", "time": 1756708800000},
        },
    ]
}

_SITIOS: dict[str, Any] = {
    "site_collection": [
        {"id": "GRA_14541_2025", "title": "GIIROB-RIN 2025-2026", "type": "siteupv"},
        {"id": "CEN_R_2025", "title": "D.A. Etsinf", "type": "sitecen"},
        {"id": "GRA_14530_2023", "title": "Estadistica vieja", "type": "siteupv"},
    ]
}


class _SakaiFalso:
    """Sustituye a SakaiClient sin tocar la red ni el llavero."""

    def __init__(self, settings: Settings) -> None:
        self.pedidas: list[str] = []

    async def __aenter__(self) -> _SakaiFalso:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def get_json(self, path: str, **params: Any) -> Any:  # noqa: ANN401 - JSON de Sakai
        self.pedidas.append(path)
        if "assignment" in path:
            return _TAREAS
        if "calendar" in path:
            return _CALENDARIO
        return _SITIOS


@pytest.fixture
def source(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> PoliformatSource:
    monkeypatch.setattr("upv_mcp.sources.poliformat.SakaiClient", _SakaiFalso)
    settings = Settings(schedule_ics_file=tmp_path / "h.ics", data_dir=tmp_path)
    return PoliformatSource(settings)


def test_parse_site_id() -> None:
    """Solo GRA_<codigo>_<curso> es una asignatura.

    CEN_R_* (centro) y CDL_0_* (centro de lenguas) son sitios reales de la cuenta
    pero no asignaturas, y sus "entregas" no deben salir en las respuestas.
    """
    assert parse_site_id("GRA_14541_2025") == ("14541", 2025)
    assert parse_site_id("CEN_R_2025") is None
    assert parse_site_id("CDL_0_2023") is None
    assert parse_site_id("basura") is None


async def test_convierte_tareas_en_entregas(source: PoliformatSource) -> None:
    payload = await source.fetch()
    entrega = next(a for a in payload.assignments if a.uid.endswith("aaa-111"))

    assert entrega.course.code == "14541"
    assert entrega.source is SourceName.POLIFORMAT
    # 11:00 UTC en abril son las 13:00 en Valencia.
    assert entrega.due == datetime(2026, 4, 15, 13, 0, tzinfo=MADRID)


async def test_decodifica_las_entidades_html(source: PoliformatSource) -> None:
    """Sakai devuelve 'Pr&aacute;ctica01', no 'Practica01'."""
    payload = await source.fetch()
    entrega = next(a for a in payload.assignments if a.uid.endswith("aaa-111"))

    assert entrega.title == "Práctica01-PL-3A1"


async def test_ninguna_entrega_se_clasifica_como_examen(source: PoliformatSource) -> None:
    """Ni todo examen es tarea ni toda tarea es examen: no se adivina por titulo."""
    payload = await source.fetch()

    assert payload.assignments
    assert all(a.kind is EventKind.ASSIGNMENT for a in payload.assignments)


async def test_descarta_el_evento_que_duplica_una_tarea(source: PoliformatSource) -> None:
    """48 de los 106 eventos reales llevan assignmentId: son tareas repetidas."""
    payload = await source.fetch()
    uids = [a.uid for a in payload.assignments]

    assert not any("evt-dup" in u for u in uids)
    assert any("evt-propio" in u for u in uids), "la fecha limite propia si debe entrar"


async def test_ignora_los_eventos_de_tipo_actividad(source: PoliformatSource) -> None:
    payload = await source.fetch()

    assert not any("evt-actividad" in a.uid for a in payload.assignments)


async def test_solo_el_curso_academico_mas_reciente(source: PoliformatSource) -> None:
    """Arrastrar entregas de hace dos cursos solo mete ruido."""
    payload = await source.fetch()

    assert all(a.course.code != "14530" for a in payload.assignments)


async def test_los_dos_formatos_de_fecha_de_sakai(source: PoliformatSource) -> None:
    """Tareas usan epochSecond; el calendario usa `time` en MILIsegundos.

    Confundirlos coloca los eventos en 1970 o en el ano 58000.
    """
    payload = await source.fetch()
    del_calendario = next(a for a in payload.assignments if "evt-propio" in a.uid)

    assert del_calendario.due.year == 2026
    assert del_calendario.due.month == 6


async def test_descarta_tareas_sin_fecha_limite(source: PoliformatSource) -> None:
    payload = await source.fetch()

    assert not any("ccc-333" in a.uid for a in payload.assignments)


async def test_resultado_ordenado_y_sin_duplicados(source: PoliformatSource) -> None:
    payload = await source.fetch()
    fechas = [a.due for a in payload.assignments]

    assert fechas == sorted(fechas)
    assert len({a.uid for a in payload.assignments}) == len(payload.assignments)


def test_filtra_la_basura_de_proyectos_subidos_enteros() -> None:
    """Un profesor sube un repo de PyCharm y aparecen 200 ficheros de .idea/."""
    from upv_mcp.sources.poliformat import _es_ruido

    base = "https://poliformat.upv.es/access/content/group/GRA_14536_2025"
    assert _es_ruido(".gitignore", f"{base}/proyecto/.gitignore")
    assert _es_ruido("workspace.xml", f"{base}/proyecto/.idea/workspace.xml")
    assert _es_ruido("cache.pyc", f"{base}/x/__pycache__/cache.pyc")
    assert _es_ruido("nb.ipynb", f"{base}/x/.ipynb_checkpoints/nb.ipynb")

    assert not _es_ruido("Tema 1.pdf", f"{base}/Tema 1.pdf")
    assert not _es_ruido("practica.ipynb", f"{base}/cuadernos/practica.ipynb")


# -- Estado de entrega -----------------------------------------------------------

_CON_ENTREGAS: dict[str, Any] = {
    "assignment_collection": [
        {
            "id": "hecha",
            "title": "Practica entregada",
            "context": "GRA_14541_2025",
            "dueTimeString": "2026-03-24T10:00:00Z",
            "gradeScaleMaxPoints": "10,00",
            "submissions": [
                {
                    "submitted": True,
                    "userSubmission": True,
                    "dateSubmittedEpochSeconds": 1774387603,
                    "late": False,
                    "graded": True,
                    "grade": "8,30",
                    "feedbackComment": "<p>Buen trabajo, revisa el <b>apartado 3</b>.</p>",
                }
            ],
        },
        {
            "id": "sin-hacer",
            "title": "Practica sin entregar",
            "context": "GRA_14541_2025",
            "dueTimeString": "2026-04-01T10:00:00Z",
            "submissions": [
                {
                    # `submitted` vale True hasta en las que no se han entregado:
                    # es el campo trampa de esta API.
                    "submitted": True,
                    "userSubmission": False,
                    "dateSubmittedEpochSeconds": None,
                    "graded": False,
                    "grade": "",
                }
            ],
        },
        {
            "id": "sin-registro",
            "title": "Tarea sin objeto de entrega",
            "context": "GRA_14541_2025",
            "dueTimeString": "2026-04-08T10:00:00Z",
            "submissions": None,
        },
        {
            # Corregida con un 9,50 pero sin entrega a nombre de este alumno: es
            # como se ve un trabajo de grupo, donde entrega un miembro. Datos
            # reales: el proyecto de IHM y cuatro entregas de PR3.
            "id": "grupo",
            "title": "Entrega proyecto en grupo",
            "context": "GRA_14541_2025",
            "dueTimeString": "2026-04-15T10:00:00Z",
            "gradeScaleMaxPoints": "10,00",
            "submissions": [
                {
                    "submitted": True,
                    "userSubmission": False,
                    "dateSubmittedEpochSeconds": None,
                    "graded": True,
                    "grade": "9,50",
                }
            ],
        },
    ]
}


@pytest.fixture
def source_entregas(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> PoliformatSource:
    class _Falso(_SakaiFalso):
        async def get_json(self, path: str, **params: Any) -> Any:  # noqa: ANN401
            if "assignment" in path:
                return _CON_ENTREGAS
            if "calendar" in path:
                return {"calendar_collection": []}
            return _SITIOS

    monkeypatch.setattr("upv_mcp.sources.poliformat.SakaiClient", _Falso)
    settings = Settings(schedule_ics_file=tmp_path / "h.ics", data_dir=tmp_path)
    return PoliformatSource(settings)


async def test_detecta_entrega_hecha_con_nota(source_entregas: PoliformatSource) -> None:
    payload = await source_entregas.fetch()
    hecha = next(a for a in payload.assignments if a.uid.endswith("hecha"))

    assert hecha.submission is not None
    assert hecha.submission.status is SubmissionStatus.SUBMITTED
    assert hecha.submission.submitted_at is not None
    assert hecha.submission.late is False
    assert hecha.submission.graded is True
    assert hecha.submission.grade == "8,30"
    assert hecha.submission.grade_max == "10,00", "una nota sin su escala no informa"
    assert hecha.submission.feedback == "Buen trabajo, revisa el apartado 3."
    assert hecha.is_pending is False


async def test_detecta_entrega_sin_hacer(source_entregas: PoliformatSource) -> None:
    payload = await source_entregas.fetch()
    falta = next(a for a in payload.assignments if a.uid.endswith("sin-hacer"))

    assert falta.submission is not None
    assert falta.submission.status is SubmissionStatus.NOT_SUBMITTED
    assert falta.submission.grade is None
    assert falta.is_pending is True


async def test_sin_registro_es_desconocido_no_pendiente(
    source_entregas: PoliformatSource,
) -> None:
    """6 de 82 tareas reales no traen registro de entrega.

    Decirle a un estudiante que le falta entregar algo que quiza ya hizo es peor
    que reconocer que no se sabe.
    """
    payload = await source_entregas.fetch()
    desconocida = next(a for a in payload.assignments if a.uid.endswith("sin-registro"))

    assert desconocida.submission is not None
    assert desconocida.submission.status is SubmissionStatus.UNKNOWN
    assert desconocida.is_pending is False, "unknown NUNCA cuenta como pendiente"


async def test_corregida_sin_registro_de_entrega_no_es_pendiente(
    source_entregas: PoliformatSource,
) -> None:
    """Si tiene un 9,50, el trabajo existe: decir "te falta entregarla" es falso.

    Sakai da `userSubmission` false y `graded` true a la vez en el trabajo de grupo
    (6 casos reales). Marcarlo NOT_SUBMITTED lo colaba en `pending_only`, que es
    justo la pregunta "que me queda por hacer".
    """
    payload = await source_entregas.fetch()
    grupo = next(a for a in payload.assignments if a.uid.endswith("grupo"))

    assert grupo.submission is not None
    assert grupo.submission.status is SubmissionStatus.UNKNOWN, (
        "no consta entrega suya, pero NOT_SUBMITTED afirmaria algo falso"
    )
    assert grupo.submission.graded is True
    assert grupo.submission.grade == "9,50", "la nota se conserva"
    assert grupo.is_pending is False


async def test_ignora_el_campo_submitted_que_miente(
    source_entregas: PoliformatSource,
) -> None:
    """`submitted` vale True en las 76 tareas reales, incluidas las no entregadas.

    Fiarse de el habria dicho al estudiante que lo tiene todo hecho. El campo
    fiable es `userSubmission`. Verificado contra la API real.
    """
    payload = await source_entregas.fetch()
    falta = next(a for a in payload.assignments if a.uid.endswith("sin-hacer"))

    assert falta.submission is not None
    assert falta.submission.status is SubmissionStatus.NOT_SUBMITTED
