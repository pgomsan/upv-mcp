"""Tests de los materiales (MCP resources) y de la tool de anuncios."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from upv_mcp.cache.db import CacheRepository
from upv_mcp.config import Settings
from upv_mcp.models import Announcement, Course, CourseSite, Material
from upv_mcp.repository import AcademicRepository
from upv_mcp.tools.announcements import list_announcements
from upv_mcp.tools.materials import render_course, render_index

MADRID = ZoneInfo("Europe/Madrid")

REDES = Course(code="14541", name="Redes Industriales", acronym="RIN")
VISION = Course(code="14537", name="Visión por Computador", acronym="VC")


class _RepoEnFecha(AcademicRepository):
    def __init__(self, *args: object, momento: datetime, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._momento = momento

    def now(self) -> datetime:
        return self._momento

    async def ensure_fresh(self, *, force: bool = False) -> None:
        return None  # La cache la rellenan los tests a mano.

    async def ensure_materials(self, course_code: str) -> bool:
        return True  # Sin red: los materiales ya estan en la cache.


@pytest.fixture
def repo(tmp_path: Path, horario_ics: Path) -> Iterator[AcademicRepository]:
    settings = Settings(schedule_ics_file=horario_ics, data_dir=tmp_path / "d")
    cache = CacheRepository(settings.db_path)
    cache.replace_course_sites(
        "poliformat",
        [
            CourseSite(course=REDES, site_id="GRA_14541_2025"),
            CourseSite(course=VISION, site_id="GRA_14537_2025"),
        ],
    )
    cache.replace_materials(
        "poliformat",
        [
            Material(
                course=REDES,
                title="Tema 1 - Introduccion.pdf",
                url="https://poliformat.upv.es/access/content/group/GRA_14541_2025/t1.pdf",
                content_type="application/pdf",
                updated_at=datetime(2026, 2, 4, 9, 14, tzinfo=MADRID),
                size_bytes=2_500_000,
            ),
            Material(
                course=REDES,
                title="Practicas",
                url="https://poliformat.upv.es/access/content/group/GRA_14541_2025/pl/",
                content_type="collection",
            ),
            Material(
                course=VISION,
                title="Enunciado P1.docx",
                url="https://poliformat.upv.es/access/content/group/GRA_14537_2025/p1.docx",
                content_type="application/vnd.openxmlformats-officedocument"
                ".wordprocessingml.document",
                size_bytes=45_000,
            ),
        ],
    )
    cache.replace_announcements(
        "poliformat",
        [
            Announcement(
                uid="a1",
                course=REDES,
                title="Examen recuperacion",
                body="El dia 30 de junio a las 11:00h tendra lugar la recuperacion.",
                author="Leopoldo Armesto",
                published_at=datetime(2026, 6, 20, 12, 0, tzinfo=MADRID),
            ),
            Announcement(
                uid="a2",
                course=VISION,
                title="Aviso antiguo",
                body="Esto es de hace mucho.",
                published_at=datetime(2025, 10, 1, 9, 0, tzinfo=MADRID),
            ),
        ],
    )
    repo = _RepoEnFecha(settings, cache, momento=datetime(2026, 6, 25, tzinfo=MADRID))
    yield repo
    cache.close()


# -- Materiales (resources) ------------------------------------------------------


async def test_el_indice_lista_las_asignaturas_con_su_uri(repo: AcademicRepository) -> None:
    texto = await render_index(repo)

    assert "Redes Industriales" in texto
    assert "upv://materiales/14541" in texto
    assert "upv://materiales/14537" in texto


async def test_el_listado_separa_ficheros_de_carpetas(repo: AcademicRepository) -> None:
    texto = await render_course(repo, "14541")

    assert "Tema 1 - Introduccion.pdf" in texto
    assert "## Ficheros" in texto
    assert "## Carpetas" in texto
    assert "[carpeta] Practicas" in texto


async def test_el_listado_trae_la_url_pero_no_el_fichero(repo: AcademicRepository) -> None:
    """Nunca se descarga el contenido: solo metadatos y enlace."""
    texto = await render_course(repo, "14541")

    assert "https://poliformat.upv.es/access/content/group/GRA_14541_2025/t1.pdf" in texto
    assert "2.4 MB" in texto
    assert "actualizado 2026-02-04" in texto


async def test_asignatura_sin_materiales_orienta(repo: AcademicRepository) -> None:
    """Un codigo equivocado no debe devolver un vacio mudo."""
    texto = await render_course(repo, "99999")

    assert "no tiene ningun recurso" in texto


async def test_sin_poliformat_el_indice_explica_como_configurarlo(
    tmp_path: Path, horario_ics: Path
) -> None:
    settings = Settings(schedule_ics_file=horario_ics, data_dir=tmp_path / "vacio")
    with CacheRepository(settings.db_path) as cache:
        repo = _RepoEnFecha(settings, cache, momento=datetime(2026, 6, 25, tzinfo=MADRID))
        texto = await render_index(repo)

    assert "upv-mcp-config set poliformat" in texto


# -- Anuncios --------------------------------------------------------------------


async def test_anuncios_del_mas_reciente_al_mas_antiguo(repo: AcademicRepository) -> None:
    resultado = await list_announcements(repo, 365, limit=50)

    fechas = [a.published_at for a in resultado.announcements]
    assert fechas == sorted(fechas, reverse=True)


async def test_anuncios_respeta_la_ventana_temporal(repo: AcademicRepository) -> None:
    resultado = await list_announcements(repo, 30, limit=50)

    assert len(resultado.announcements) == 1
    assert resultado.announcements[0].title == "Examen recuperacion"


async def test_anuncios_trae_asignatura_y_autor(repo: AcademicRepository) -> None:
    resultado = await list_announcements(repo, 30, limit=50)
    aviso = resultado.announcements[0]

    assert aviso.course.name == "Redes Industriales"
    assert aviso.author == "Leopoldo Armesto"


async def test_anuncios_trunca_y_lo_dice(repo: AcademicRepository) -> None:
    resultado = await list_announcements(repo, 365, limit=1)

    assert len(resultado.announcements) == 1
    assert resultado.meta.total_matching == 2
    assert resultado.meta.truncated is True


async def test_anuncios_vacio_no_es_error(repo: AcademicRepository) -> None:
    resultado = await list_announcements(repo, 1, limit=50)

    assert resultado.announcements == []
    assert resultado.meta.truncated is False


async def test_anuncios_valida_la_ventana(repo: AcademicRepository) -> None:
    with pytest.raises(ValueError, match="entre 1 y 365"):
        await list_announcements(repo, 0, limit=50)
    with pytest.raises(ValueError, match="entre 1 y 365"):
        await list_announcements(repo, 400, limit=50)


async def test_el_listado_se_trunca_y_lo_dice(
    tmp_path: Path, horario_ics: Path
) -> None:
    """Una asignatura real llega a 1159 recursos: volcarlos seria inaceptable."""
    settings = Settings(schedule_ics_file=horario_ics, data_dir=tmp_path / "muchos")
    with CacheRepository(settings.db_path) as cache:
        cache.replace_materials(
            "poliformat",
            [
                Material(
                    course=REDES,
                    title=f"Fichero {i:04d}.pdf",
                    url=f"https://poliformat.upv.es/x/{i}.pdf",
                    content_type="application/pdf",
                )
                for i in range(500)
            ],
        )
        repo = _RepoEnFecha(settings, cache, momento=datetime(2026, 6, 25, tzinfo=MADRID))
        texto = await render_course(repo, "14541", limit=10)

    assert texto.count("Fichero ") == 10
    assert "se muestran 10 de 500" in texto
    assert "en vez de presentar la lista como completa" in texto


async def test_el_indice_concuerda_en_singular(repo: AcademicRepository) -> None:
    texto = await render_index(repo)

    assert "1 recurso -" in texto
    assert "1 recursos" not in texto
