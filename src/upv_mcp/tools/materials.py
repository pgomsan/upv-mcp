"""Materiales de asignatura, expuestos como MCP *resources*.

Por que resources y no una tool: los apuntes son contenido navegable que el cliente
decide cuando leer, no una accion que ejecutar. Una tool obligaria al modelo a
"llamar" para mirar; un resource se lista y se lee cuando hace falta.

Nunca se devuelve el contenido de los ficheros, solo sus metadatos y la URL. Una
sola asignatura tiene 37 PDFs: volcarlos al contexto seria justo lo contrario de lo
que persigue este servidor.
"""

from __future__ import annotations

from upv_mcp.models import Material
from upv_mcp.repository import AcademicRepository

#: URI del indice de asignaturas con materiales.
INDEX_URI = "upv://materiales"

#: Plantilla por asignatura. El parametro es el codigo UPV (p.ej. 14541).
COURSE_URI_TEMPLATE = "upv://materiales/{course_code}"


def _plural(cantidad: int, singular: str) -> str:
    return f"{cantidad} {singular}" if cantidad == 1 else f"{cantidad} {singular}s"


def _tamano(bytes_: int | None) -> str:
    if not bytes_:
        return ""
    if bytes_ < 1024:
        return f" ({bytes_} B)"
    if bytes_ < 1024 * 1024:
        return f" ({bytes_ / 1024:.0f} KB)"
    return f" ({bytes_ / (1024 * 1024):.1f} MB)"


def _linea(material: Material) -> str:
    marca = "[carpeta]" if material.is_folder else f"[{material.content_type or 'fichero'}]"
    fecha = f" - actualizado {material.updated_at:%Y-%m-%d}" if material.updated_at else ""
    return f"- {marca} {material.title}{_tamano(material.size_bytes)}{fecha}\n  {material.url}"


async def render_index(repo: AcademicRepository) -> str:
    """Indice en Markdown de las asignaturas que tienen materiales."""
    await repo.ensure_fresh()
    cursos = repo.cache.material_courses()

    if not cursos:
        return (
            "# Materiales\n\n"
            "No hay materiales cacheados. Si PoliformaT no esta configurado, ejecuta "
            "`upv-mcp-config set poliformat`.\n"
        )

    lineas = ["# Materiales por asignatura\n"]
    for curso in cursos:
        total = len(repo.cache.materials(curso.code))
        lineas.append(
            f"- **{curso.name}** ({curso.code}) - {_plural(total, 'recurso')} - "
            f"`{COURSE_URI_TEMPLATE.format(course_code=curso.code)}`"
        )
    return "\n".join(lineas) + "\n"


#: Tope de entradas por asignatura. Una sola llega a tener 1159 recursos, y
#: volcarlos seria justo lo contrario de lo que persigue este servidor.
MAX_ENTRIES = 60


async def render_course(
    repo: AcademicRepository, course_code: str, *, limit: int = MAX_ENTRIES
) -> str:
    """Listado en Markdown de los materiales de una asignatura."""
    await repo.ensure_fresh()
    materiales = repo.cache.materials(course_code)

    if not materiales:
        conocidas = ", ".join(c.code for c in repo.cache.material_courses()) or "ninguna"
        return (
            f"# Materiales de {course_code}\n\n"
            f"No hay materiales para esa asignatura. Con materiales: {conocidas}.\n"
        )

    nombre = materiales[0].course.name
    carpetas = [m for m in materiales if m.is_folder]
    ficheros = [m for m in materiales if not m.is_folder]

    partes = [
        f"# {nombre} ({course_code})\n",
        f"{_plural(len(ficheros), 'fichero')}, {_plural(len(carpetas), 'carpeta')}\n",
    ]
    if ficheros:
        partes.append("## Ficheros\n")
        partes.extend(_linea(m) for m in ficheros[:limit])
        if len(ficheros) > limit:
            partes.append(
                f"\n_Listado recortado: se muestran {limit} de {len(ficheros)} ficheros. "
                "Diselo al usuario en vez de presentar la lista como completa._"
            )
    if carpetas:
        partes.append("\n## Carpetas\n")
        partes.extend(_linea(m) for m in carpetas[:limit])
        if len(carpetas) > limit:
            partes.append(f"\n_...y {len(carpetas) - limit} carpetas mas._")
    return "\n".join(partes) + "\n"
