"""Tests de la extraccion de texto de los ficheros de PoliformaT."""

from __future__ import annotations

import io
import zipfile

import pytest

from upv_mcp.sources.extract import MAX_CHARS, ExtractionError, extract


def _pdf_sin_texto() -> bytes:
    """PDF valido de dos paginas en blanco: como uno escaneado, sin texto extraible."""
    from pypdf import PdfWriter

    escritor = PdfWriter()
    escritor.add_blank_page(width=200, height=200)
    escritor.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    escritor.write(buffer)
    return buffer.getvalue()


def _zip_falso(interior: str) -> bytes:
    """Un .pptx o .docx es un zip con una estructura concreta dentro."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr(interior, "x")
    return buffer.getvalue()


def test_texto_plano() -> None:
    texto, recortado = extract(b"hola mundo", "text/plain", "notas.txt")

    assert texto == "hola mundo"
    assert recortado is False


def test_recorta_los_documentos_largos() -> None:
    largo = ("palabra " * 20_000).encode()
    texto, recortado = extract(largo, "text/plain", "largo.txt")

    assert recortado is True
    assert len(texto) <= MAX_CHARS


def test_manda_la_firma_del_fichero_y_no_la_extension() -> None:
    """En PoliformaT hay un "PresentacionRIN.pdf" que es un PowerPoint.

    Fiarse de la extension hacia que pypdf reventara con un error incomprensible.
    """
    pptx = _zip_falso("ppt/presentation.xml")

    # Se llama .pdf y Sakai lo declara PDF, pero los bytes dicen otra cosa.
    with pytest.raises(ExtractionError) as info:
        extract(pptx, "application/pdf", "PresentacionRIN.pdf")

    # El error es de PowerPoint, no de PDF: se ha reconocido bien el formato.
    assert "presentacion" in str(info.value).lower()


def test_un_zip_normal_se_rechaza_con_motivo() -> None:
    corriente = _zip_falso("carpeta/fichero.txt")

    with pytest.raises(ExtractionError, match="No se puede extraer texto"):
        extract(corriente, "application/zip", "practica.zip")


def test_formato_no_soportado_dice_cual_es() -> None:
    with pytest.raises(ExtractionError) as info:
        extract(b"\x89PNG\r\n\x1a\n", "image/png", "diagrama.png")

    mensaje = str(info.value)
    assert "image/png" in mensaje
    assert "PDF, PowerPoint" in mensaje, "debe decir que SI se puede leer"


def test_pdf_sin_texto_lo_explica() -> None:
    """Un PDF escaneado no tiene texto: hay que decirlo, no devolver vacio."""
    with pytest.raises(ExtractionError) as info:
        extract(_pdf_sin_texto(), "application/pdf", "escaneado.pdf")

    assert "OCR" in str(info.value)


def test_reconoce_el_pdf_por_su_cabecera() -> None:
    """Aunque el nombre no lleve extension ni el tipo sea correcto."""
    with pytest.raises(ExtractionError) as info:
        extract(_pdf_sin_texto(), "application/octet-stream", "sin_extension")

    assert "OCR" in str(info.value), "se trato como PDF, que es lo correcto"
