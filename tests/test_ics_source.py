"""Tests del parser .ics contra la estructura real del generador de la UPV."""

from __future__ import annotations

from pathlib import Path

import pytest

from upv_mcp.config import Settings
from upv_mcp.models import ClassSession, EventKind
from upv_mcp.sources.base import SourceError
from upv_mcp.sources.ics import IcsSource, _parse_groups


def _parse(path: Path, settings: Settings, name: str = "schedule") -> list[ClassSession]:
    return IcsSource(settings).parse(path.read_text(encoding="utf-8"), name).sessions


def test_extrae_asignatura_del_html_de_description(
    horario_ics: Path, settings: Settings
) -> None:
    """El nombre NO esta en SUMMARY (son siglas), sino en el <b> del DESCRIPTION."""
    sessions = _parse(horario_ics, settings)
    est = next(s for s in sessions if s.course.acronym == "EST")
    assert est.course.name == "Estadística"
    assert est.course.code == "14530"
    assert est.teaching_type == "TA/TS"


def test_convierte_utc_a_hora_local_de_valencia(horario_ics: Path, settings: Settings) -> None:
    """13:00Z en verano y 14:00Z en invierno son la MISMA clase: las 15:00 en Madrid.

    Es el bug mas facil de introducir aqui, y el generador de la UPV si ajusta el
    cambio de hora, asi que basta convertir sin inventar correcciones.
    """
    sessions = _parse(horario_ics, settings)
    est = sorted(
        (s for s in sessions if s.course.acronym == "EST"), key=lambda s: s.start
    )
    verano = next(s for s in est if s.start.month == 9)
    invierno = next(s for s in est if s.start.month == 11)

    assert verano.start.hour == 15
    assert invierno.start.hour == 15
    assert verano.start.utcoffset() != invierno.start.utcoffset()


def test_separa_aula_y_edificio(horario_ics: Path, settings: Settings) -> None:
    sessions = _parse(horario_ics, settings)
    lab = next(s for s in sessions if s.location and "ANGELA" in s.location.raw)
    assert lab.location is not None
    assert lab.location.building == "Edificio 1B"
    assert lab.location.room == "L. 1B 0.0 (ANGELA RUÍZ ROBLES)"


def test_deduplica_grupos_repetidos() -> None:
    """El export repite grupos: 'TA-2A (Castellano),TA-2A (Castellano)'."""
    assert _parse_groups("TA-2A (Castellano),TS-2A (Castellano)") == ["TA-2A", "TS-2A"]
    assert _parse_groups("PL-1 (Castellano),PL-1 (Castellano),PL-1 (Castellano)") == ["PL-1"]
    assert _parse_groups("") == []


def test_aula_vacia_no_rompe(casos_limite_ics: Path, settings: Settings) -> None:
    sessions = _parse(casos_limite_ics, settings)
    sin_aula = next(s for s in sessions if s.uid.startswith("LIMITE-SIN-AULA"))
    assert sin_aula.location is None
    assert sin_aula.teacher is None
    assert sin_aula.course.name == "Sistemas Operativos"


def test_aula_sin_edificio(casos_limite_ics: Path, settings: Settings) -> None:
    sessions = _parse(casos_limite_ics, settings)
    ev = next(s for s in sessions if s.uid.startswith("LIMITE-AULA-SIN-EDIFICIO"))
    assert ev.location is not None
    assert ev.location.room == "AUTOMATIZACION"
    assert ev.location.building is None


def test_asignatura_sin_codigo_no_pierde_el_evento(
    casos_limite_ics: Path, settings: Settings
) -> None:
    """Preferimos una asignatura sin codigo a perder la clase entera."""
    sessions = _parse(casos_limite_ics, settings)
    ev = next(s for s in sessions if s.uid.startswith("LIMITE-SIN-CODIGO"))
    assert ev.course.code == ""
    assert ev.course.name == "Asignatura sin codigo"


def test_horario_no_contiene_examenes(horario_ics: Path, settings: Settings) -> None:
    """El feed 'Horario de Clases' de la UPV son solo clases.

    Este test documenta la limitacion que obliga a `list_upcoming_deadlines` a
    declarar su cobertura.
    """
    sessions = _parse(horario_ics, settings)
    assert all(s.kind is EventKind.CLASS for s in sessions)


def test_calendario_de_examenes_se_clasifica_como_examen(
    examenes_ics: Path, settings: Settings
) -> None:
    payload = IcsSource(settings).parse(
        examenes_ics.read_text(encoding="utf-8"), "exams"
    )
    assert all(s.kind is EventKind.EXAM for s in payload.sessions)
    assert len(payload.assignments) == 2
    assert payload.assignments[0].title.startswith("Examen de")


def test_sesiones_ordenadas_cronologicamente(horario_ics: Path, settings: Settings) -> None:
    sessions = _parse(horario_ics, settings)
    assert sessions == sorted(sessions, key=lambda s: s.start)


def test_ics_invalido_da_error_claro(settings: Settings) -> None:
    with pytest.raises(SourceError, match="no es iCal valido"):
        IcsSource(settings).parse("esto no es un calendario", "schedule")
