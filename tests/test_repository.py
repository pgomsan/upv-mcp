"""Tests del orquestador: refresco, degradacion a cache y avisos de cobertura."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from upv_mcp.cache.db import CacheRepository
from upv_mcp.config import CalendarSpec, Settings
from upv_mcp.repository import AcademicRepository
from upv_mcp.sources.base import SourceError, SourcePayload
from upv_mcp.sources.ics import IcsSource


class _FuenteQueFalla(IcsSource):
    """Fuente que simula caida de red en cada lectura."""

    async def read_calendar(self, spec: CalendarSpec) -> SourcePayload:
        raise SourceError("sin red")


async def test_refresca_cuando_la_cache_esta_vacia(
    settings: Settings, cache: CacheRepository
) -> None:
    repo = AcademicRepository(settings, cache)
    await repo.ensure_fresh()

    assert not cache.is_empty()
    assert repo.serving_stale is False


async def test_no_refresca_si_la_cache_esta_fresca(
    settings: Settings, cache: CacheRepository
) -> None:
    repo = AcademicRepository(settings, cache, source=_FuenteQueFalla(settings))
    cache.replace_calendar("schedule", [], fetched_at=datetime.now(UTC))

    await repo.ensure_fresh()  # no debe intentar leer, la fuente reventaria

    assert repo.serving_stale is False


async def test_sirve_cache_antigua_si_falla_la_red(
    settings: Settings, cache: CacheRepository
) -> None:
    """Un servidor local que revienta porque no hay wifi es inutil."""
    bueno = AcademicRepository(settings, cache)
    await bueno.ensure_fresh()
    total = cache.count_sessions_between(
        datetime(2024, 1, 1, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC)
    )

    roto = AcademicRepository(settings, cache, source=_FuenteQueFalla(settings))
    await roto.ensure_fresh(force=True)

    assert roto.serving_stale is True
    assert (
        cache.count_sessions_between(
            datetime(2024, 1, 1, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC)
        )
        == total
    )
    assert "cache local" in (roto.coverage_note() or "")


async def test_propaga_el_error_si_no_hay_nada_que_servir(
    settings: Settings, cache: CacheRepository
) -> None:
    """Sin cache y sin red no se puede fingir: hay que fallar."""
    repo = AcademicRepository(settings, cache, source=_FuenteQueFalla(settings))
    with pytest.raises(SourceError):
        await repo.ensure_fresh()


async def test_avisa_de_que_no_hay_examenes_ni_entregas(
    settings: Settings, cache: CacheRepository
) -> None:
    """El aviso evita que el modelo diga 'no tienes examenes' sin haberlos mirado."""
    repo = AcademicRepository(settings, cache)
    nota = repo.coverage_note() or ""

    assert "NO tienes acceso al calendario de examenes" in nota
    assert "PoliformaT" in nota


async def test_con_calendario_de_examenes_el_aviso_se_reduce(
    tmp_path: Path, horario_ics: Path, examenes_ics: Path, cache: CacheRepository
) -> None:
    settings = Settings(
        schedule_ics_file=horario_ics,
        exams_ics_file=examenes_ics,
        data_dir=tmp_path / "data",
    )
    repo = AcademicRepository(settings, cache)
    nota = repo.coverage_note() or ""

    assert "NO tienes acceso al calendario de examenes" not in nota
    assert "PoliformaT" in nota


async def test_caducidad_dispara_refresco(settings: Settings, cache: CacheRepository) -> None:
    repo = AcademicRepository(settings, cache)
    cache.replace_calendar(
        "schedule", [], fetched_at=datetime.now(UTC) - timedelta(days=1)
    )

    await repo.ensure_fresh()

    assert not cache.is_empty()
