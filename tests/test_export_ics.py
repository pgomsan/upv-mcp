"""Generador del .ics de entregas (`upv_mcp.export_ics`).

No confundir con `test_ics_source.py`, que prueba el PARSER del horario de la UPV.

Los datos son sinteticos. El golden de `tests/data/` se compara byte a byte; para
regenerarlo a proposito: `uv run pytest tests/test_export_ics.py --update-golden`.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from upv_mcp import export_ics
from upv_mcp.export_ics import _debe_publicarse, _escapar, _plegar, build_calendar
from upv_mcp.models import (
    Assignment,
    Course,
    EventKind,
    SourceName,
    Submission,
    SubmissionStatus,
)

GOLDEN = Path(__file__).parent / "data" / "export_ics_golden.ics"
MADRID = ZoneInfo("Europe/Madrid")
NOW = datetime(2026, 9, 22, 11, 0, tzinfo=MADRID)

ICD = Course(code="14534", name="Infraestructura Informática para Centros de Datos", acronym="ICD")


def _entrega(
    uid: str = "poliformat:assignment:aaaa-0001",
    *,
    title: str = "Práctica 1",
    course: Course = ICD,
    due: datetime | None = None,
    url: str | None = "https://poliformat.upv.es/direct/assignment/aaaa-0001",
    status: SubmissionStatus | None = SubmissionStatus.NOT_SUBMITTED,
    graded: bool = False,
    kind: EventKind = EventKind.ASSIGNMENT,
) -> Assignment:
    return Assignment(
        uid=uid,
        kind=kind,
        title=title,
        course=course,
        due=due or datetime(2026, 10, 19, 19, 9, tzinfo=MADRID),
        url=url,
        source=SourceName.POLIFORMAT,
        submission=None if status is None else Submission(status=status, graded=graded),
    )


def _representativas() -> list[Assignment]:
    return [
        _entrega(
            "poliformat:assignment:aaaa-0001",
            status=SubmissionStatus.UNKNOWN,
        ),
        # Sin siglas en el curso: salen de ACRONIMOS. Horario de invierno (+01:00).
        _entrega(
            "poliformat:assignment:bbbb-0002",
            title="Memoria final",
            course=Course(code="14548", name="Aprendizaje Automático"),
            due=datetime(2026, 11, 3, 23, 55, tzinfo=MADRID),
            url="https://poliformat.upv.es/direct/assignment/bbbb-0002",
            status=SubmissionStatus.NOT_SUBMITTED,
        ),
        # Acentos y coma; sin submission ni URL (evento Deadline de PoliformaT);
        # 00:30 local es el dia anterior en UTC; codigo fuera de ACRONIMOS.
        _entrega(
            "poliformat:event:cccc-0003",
            title="Diseño, simulación y validación del brazo robótico articulado",
            course=Course(code="14999", name="Robótica Avanzada"),
            due=datetime(2026, 12, 1, 0, 30, tzinfo=MADRID),
            url=None,
            status=None,
        ),
    ]


def _desplegar(ics: str) -> list[str]:
    return ics.replace("\r\n ", "").split("\r\n")


# -- Golden ---------------------------------------------------------------------------


def test_golden_byte_a_byte(request: pytest.FixtureRequest) -> None:
    salida = build_calendar(_representativas(), now=NOW).encode("utf-8")
    if request.config.getoption("--update-golden"):
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_bytes(salida)
        pytest.skip(f"Golden regenerado en {GOLDEN}: revisa el diff antes de commitear.")
    assert salida == GOLDEN.read_bytes()


def test_dos_llamadas_iguales_dan_los_mismos_bytes() -> None:
    primera = build_calendar(_representativas(), now=NOW)
    segunda = build_calendar(_representativas(), now=NOW)
    assert primera.encode("utf-8") == segunda.encode("utf-8")
    # Tampoco depende del orden en que lleguen.
    assert build_calendar(list(reversed(_representativas())), now=NOW) == primera


def test_todas_las_lineas_terminan_en_crlf() -> None:
    salida = build_calendar(_representativas(), now=NOW)
    assert salida.endswith("\r\n")
    assert "\n" not in salida.replace("\r\n", "")
    assert "\r" not in salida.replace("\r\n", "")


def test_calendario_vacio_solo_trae_cabeceras() -> None:
    lineas = _desplegar(build_calendar([], now=NOW))
    assert lineas == [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//upv-mcp//Entregas UPV//ES",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Entregas UPV",
        "X-PUBLISHED-TTL:PT1H",
        "END:VCALENDAR",
        "",
    ]


# -- Plegado y escapado ---------------------------------------------------------------


def test_plegado_cuenta_octetos_no_caracteres() -> None:
    titulo = "Diseño óptimo de la ubicación: análisis final"
    salida = build_calendar([_entrega(title=titulo)], now=NOW)

    summary = next(linea for linea in _desplegar(salida) if linea.startswith("SUMMARY:"))
    # El caso tiene que ser el interesante: cabe en caracteres, no en octetos.
    assert len(summary) <= 75 < len(summary.encode("utf-8"))

    fisicas = salida.split("\r\n")
    assert all(len(linea.encode("utf-8")) <= 75 for linea in fisicas)
    assert summary not in fisicas, "la linea deberia haberse plegado"


@pytest.mark.parametrize("texto", ["ñ" * 100, "a" + "é" * 80, "x" * 200, "€" * 60])
def test_plegar_nunca_corta_un_caracter_ni_pasa_de_75(texto: str) -> None:
    plegada = _plegar(texto)
    fisicas = plegada.split("\r\n")
    assert all(len(f.encode("utf-8")) <= 75 for f in fisicas)
    assert all(f.startswith(" ") for f in fisicas[1:])
    assert plegada.replace("\r\n ", "") == texto


def test_plegar_no_toca_lineas_cortas() -> None:
    assert _plegar("SUMMARY:corta") == "SUMMARY:corta"
    assert _plegar("x" * 75) == "x" * 75


def test_escapado_rfc5545() -> None:
    assert _escapar("a\\b;c,d\ne\r\nf") == "a\\\\b\\;c\\,d\\ne\\nf"


def test_escapado_se_aplica_al_summary() -> None:
    salida = build_calendar([_entrega(title="Parte 1; parte 2, y \\final")], now=NOW)
    assert "SUMMARY:Entrega ICD (Parte 1\\; parte 2\\, y \\\\final) - 19:09" in _desplegar(salida)


# -- Contenido de cada evento ---------------------------------------------------------


def test_uid_tiene_formato_fijo() -> None:
    """Si esto cambia, cada cliente suscrito duplica todos los eventos."""
    salida = build_calendar([_entrega("poliformat:assignment:aaaa-0001")], now=NOW)
    assert "UID:poliformat:assignment:aaaa-0001@upv-mcp.local" in _desplegar(salida)
    assert export_ics.UID_DOMAIN == "upv-mcp.local"


def test_evento_de_todo_el_dia_en_fecha_local() -> None:
    # 00:30 del 1 de diciembre en Madrid es 30 de noviembre en UTC.
    salida = build_calendar([_entrega(due=datetime(2026, 12, 1, 0, 30, tzinfo=MADRID))], now=NOW)
    lineas = _desplegar(salida)
    assert "DTSTART;VALUE=DATE:20261201" in lineas
    assert "DTEND;VALUE=DATE:20261202" in lineas
    assert any(linea.endswith(" - 00:30") for linea in lineas if linea.startswith("SUMMARY:"))


def test_hora_del_summary_es_local_aunque_llegue_en_utc() -> None:
    utc = datetime(2026, 10, 19, 17, 9, tzinfo=ZoneInfo("UTC"))
    lineas = _desplegar(build_calendar([_entrega(due=utc)], now=NOW))
    assert "SUMMARY:Entrega ICD (Práctica 1) - 19:09" in lineas


def test_dtstamp_sale_de_now_en_utc() -> None:
    lineas = _desplegar(build_calendar([_entrega()], now=NOW))
    assert "DTSTAMP:20260922T090000Z" in lineas


def test_now_sin_zona_horaria_se_rechaza() -> None:
    with pytest.raises(ValueError):
        build_calendar([], now=datetime(2026, 9, 22, 11, 0))


def test_description_lleva_la_url_y_se_omite_sin_ella() -> None:
    con = _desplegar(build_calendar([_entrega()], now=NOW))
    assert "DESCRIPTION:https://poliformat.upv.es/direct/assignment/aaaa-0001" in con
    assert "TRANSP:TRANSPARENT" in con

    sin = _desplegar(build_calendar([_entrega(url=None)], now=NOW))
    assert not any(linea.startswith("DESCRIPTION") for linea in sin)


def test_descripciones_sustituye_al_titulo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        export_ics.DESCRIPCIONES, "poliformat:assignment:aaaa-0001", "Montar el clúster"
    )
    lineas = _desplegar(build_calendar([_entrega()], now=NOW))
    assert "SUMMARY:Entrega ICD (Montar el clúster) - 19:09" in lineas


@pytest.mark.parametrize(
    ("course", "esperada"),
    [
        (ICD, "ICD"),
        (Course(code="14534", name="Infraestructura", acronym=""), "ICD"),
        (Course(code="14534", name="Infraestructura", acronym="  "), "ICD"),
        (Course(code="99999", name="Otra"), "99999"),
        (Course(code="", name="Asignatura sin codigo"), "Asignatura sin codigo"),
    ],
    ids=["acronym", "acronym-vacio", "acronym-blanco", "codigo", "nombre"],
)
def test_etiqueta_cadena_de_fallback(course: Course, esperada: str) -> None:
    lineas = _desplegar(build_calendar([_entrega(course=course)], now=NOW))
    assert f"SUMMARY:Entrega {esperada} (Práctica 1) - 19:09" in lineas


# -- Que entra y que no ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "graded", "publica"),
    [
        (None, False, True),
        (SubmissionStatus.UNKNOWN, False, True),
        (SubmissionStatus.NOT_SUBMITTED, False, True),
        (SubmissionStatus.NOT_SUBMITTED, True, False),
        (SubmissionStatus.SUBMITTED, False, False),
        (SubmissionStatus.SUBMITTED, True, False),
    ],
    ids=[
        "sin-submission",
        "unknown",
        "not_submitted",
        "not_submitted+graded",
        "submitted",
        "submitted+graded",
    ],
)
def test_debe_publicarse(status: SubmissionStatus | None, graded: bool, publica: bool) -> None:
    assert _debe_publicarse(_entrega(status=status, graded=graded)) is publica


def test_entregadas_y_corregidas_no_aparecen() -> None:
    entregas = [
        _entrega("poliformat:assignment:pendiente"),
        _entrega("poliformat:assignment:entregada", status=SubmissionStatus.SUBMITTED),
        _entrega(
            "poliformat:assignment:corregida",
            status=SubmissionStatus.NOT_SUBMITTED,
            graded=True,
        ),
    ]
    salida = build_calendar(entregas, now=NOW)
    assert salida.count("BEGIN:VEVENT") == 1
    assert "pendiente@upv-mcp.local" in salida
    assert "entregada" not in salida
    assert "corregida" not in salida


def test_examenes_se_ignoran() -> None:
    examen = _entrega("85FDAAAcS7AADAADBZJAB9@upv.es", kind=EventKind.EXAM, status=None)
    salida = build_calendar([examen], now=NOW)
    assert "BEGIN:VEVENT" not in salida
