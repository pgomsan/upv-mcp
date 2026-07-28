"""Materiales de asignatura: navegarlos y leerlos.

Dos superficies, y la diferencia importa:

* **Resources** (`upv://materiales`, `upv://materiales/{codigo}`) para NAVEGAR: un
  listado con nombre, tipo, tamano y URL. No descarga ningun fichero. Son resources
  porque es contenido que el cliente consulta cuando le hace falta.
* **Tool `read_material`** para LEER: descarga UN fichero concreto y devuelve su
  texto, y por eso es una tool y no un resource: descargar y convertir un PDF es una
  accion con coste, no algo que se lea de pasada.

El listado nunca trae contenido: el usuario tiene 2065 recursos y una sola
asignatura llega a 1159. Se descarga solo el fichero que se pide, uno cada vez.
"""

from __future__ import annotations

from upv_mcp.models import CourseSite, Material, MaterialContentResult
from upv_mcp.repository import AcademicRepository
from upv_mcp.tools.common import build_meta

READ_DESCRIPTION = """\
Lee el CONTENIDO de un fichero de PoliformaT (apuntes, transparencias, enunciados) \
y devuelve su texto, para poder resumirlo o responder preguntas sobre el.

USALA cuando el usuario pregunte por lo que DICE un material:
- "resumeme el tema 3 de Interfaces"
- "que dice el enunciado de la practica 2"
- "explicame las transparencias de redes industriales"
- "busca en los apuntes de vision que es la homografia"

NO LA USES:
- Para saber QUE materiales hay -> eso es el resource `upv://materiales/{codigo}`, \
que lista los ficheros sin descargarlos. Esta tool descarga uno concreto.
- Para fechas de entrega o notas -> usa list_upcoming_deadlines.

PARAMETROS:
- course: asignatura, por codigo ("14544"), nombre o siglas.
- file: parte del nombre del fichero ("tema 3", "enunciado practica 2"). No hace \
falta el nombre exacto: se busca por aproximacion. Si varios encajan, se lee el \
mas probable y los demas vienen en `candidates` para que puedas preguntar cual era.

FORMATOS: PDF, PowerPoint (.pptx), Word (.docx) y texto plano. Los ZIP, imagenes y \
videos NO se pueden leer: para esos, da la URL del listado.

Un PDF escaneado (paginas que son imagenes) no tiene texto extraible y la tool lo \
dira. En ese caso di que no puedes leerlo, NO te inventes el contenido.

Si `truncated` es true, el documento era mas largo de lo que cabe: dilo en vez de \
dar por hecho que has visto el final.\
"""

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
    """Indice en Markdown de las asignaturas con materiales.

    No descarga nada: lista las asignaturas conocidas y sus URI. Los recursos de
    cada una se piden al abrir su resource.
    """
    await repo.ensure_fresh()
    sitios = repo.cache.course_sites()

    if not sitios:
        return (
            "# Materiales\n\n"
            "No hay asignaturas de PoliformaT. Si no lo has configurado, ejecuta "
            "`upv-mcp-config set poliformat`.\n"
        )

    lineas = [
        "# Materiales por asignatura\n",
        "_Abre la URI de una asignatura para ver sus ficheros._\n",
    ]
    for sitio in sitios:
        curso = sitio.course
        cacheados = len(repo.cache.materials(curso.code))
        detalle = f" - {_plural(cacheados, 'recurso')}" if cacheados else ""
        lineas.append(
            f"- **{curso.name}** ({curso.code}){detalle} - "
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
    # Descarga perezosa: solo los de esta asignatura, y solo si han caducado.
    consultado = await repo.ensure_materials(course_code)
    materiales = repo.cache.materials(course_code)

    if not materiales:
        if not consultado:
            conocidas = ", ".join(s.course.code for s in repo.cache.course_sites())
            return (
                f"# Materiales de {course_code}\n\n"
                "No se pudieron consultar. Puede que ese codigo no corresponda a "
                f"ninguna asignatura tuya (conocidas: {conocidas or 'ninguna'}) o que "
                "PoliformaT no respondiera. NO concluyas que la asignatura no tiene "
                "materiales.\n"
            )
        return (
            f"# Materiales de {course_code}\n\n"
            "Esta asignatura no tiene ningun recurso publicado en PoliformaT.\n"
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


def _puntuar(material: Material, terminos: list[str]) -> int:
    """Cuanto encaja un fichero con lo que ha pedido el usuario.

    Busqueda por aproximacion a proposito: nadie escribe "Tema3_ModelosInteraccion
    _v2.pdf", escriben "el tema 3". Se puntua por terminos encontrados en el nombre
    y en la ruta, con extra si aparecen juntos.
    """
    titulo = material.title.lower()
    ruta = material.url.lower()
    if not terminos:
        return 0

    puntos = sum(4 for t in terminos if t in titulo)
    puntos += sum(1 for t in terminos if t in ruta and t not in titulo)
    if all(t in titulo for t in terminos):
        puntos += 5
    return puntos


async def read_material(
    repo: AcademicRepository,
    course: str,
    file: str,
) -> MaterialContentResult:
    """Implementacion. `server.py` la envuelve y le pone la descripcion MCP."""
    await repo.ensure_fresh()

    sitio = _buscar_asignatura(repo, course)
    if sitio is None:
        conocidas = ", ".join(
            f"{s.course.name} ({s.course.code})" for s in repo.cache.course_sites()
        )
        raise ValueError(
            f"No encuentro la asignatura '{course}'. Las tuyas son: {conocidas or 'ninguna'}."
        )

    await repo.ensure_materials(sitio.course.code)
    materiales = [m for m in repo.cache.materials(sitio.course.code) if not m.is_folder]
    if not materiales:
        raise ValueError(f"{sitio.course.name} no tiene ficheros publicados.")

    terminos = [t for t in file.lower().split() if t]
    puntuados = sorted(
        ((_puntuar(m, terminos), m) for m in materiales),
        key=lambda par: par[0],
        reverse=True,
    )
    if puntuados[0][0] == 0:
        muestra = ", ".join(m.title for _, m in puntuados[:8])
        raise ValueError(
            f"Ningun fichero de {sitio.course.name} encaja con '{file}'. "
            f"Algunos de los que hay: {muestra}"
        )

    elegido = puntuados[0][1]
    otros = [m.title for punt, m in puntuados[1:6] if punt > 0]

    texto, recortado = await repo.read_material(elegido)

    nota = None
    if otros:
        nota = (
            f"Habia otros ficheros parecidos ({', '.join(otros[:3])}). Si el usuario "
            "buscaba otro, pregunta cual antes de responder."
        )

    return MaterialContentResult(
        material=elegido,
        text=texto,
        truncated=recortado,
        candidates=otros,
        meta=build_meta(repo, total_matching=1, returned=1, extra_note=nota),
    )


def _buscar_asignatura(repo: AcademicRepository, course: str) -> CourseSite | None:
    """Encuentra una asignatura por codigo, nombre parcial o siglas."""
    termino = course.strip().lower()
    sitios = repo.cache.course_sites()
    for sitio in sitios:
        if sitio.course.code == termino:
            return sitio
    for sitio in sitios:
        siglas = (sitio.course.acronym or "").lower()
        if termino == siglas or termino in sitio.course.name.lower():
            return sitio
    return None
