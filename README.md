# upv-mcp

Servidor [MCP](https://modelcontextprotocol.io) local que expone tu calendario
academico de la **UPV** a cualquier cliente MCP: Claude Desktop, Claude Code o
Cursor. Preguntale por tu horario en lenguaje natural y responde con tus datos
reales.

> «¿cual es mi proxima clase?»
> → *Modelado y Control de Robots, lunes 7 de septiembre a las 15:00, AULA 1E 0.3
> (Edificio 1E).*

Se alimenta del export iCal de tu horario y, opcionalmente, de PoliformaT a traves
de su **API REST oficial**. **No hace scraping y no manda tus datos a ningun sitio**:
todo ocurre en tu ordenador, en solo lectura y solo sobre tu cuenta.

## Tools

| Tool | Para que sirve |
|---|---|
| `get_schedule(start_date, end_date, course)` | Clases en un rango de fechas |
| `get_next_class()` | La siguiente sesion desde ahora, con aula y hora |
| `list_upcoming_deadlines(days_ahead, days_back, pending_only, course)` | Entregas, con estado y nota ([ver limitacion](#limitaciones)) |
| `list_announcements(days_back)` | Avisos publicados por los profesores |

## Resources

| Resource | Contenido |
|---|---|
| `upv://materiales` | Indice de asignaturas con apuntes y enunciados |
| `upv://materiales/{codigo}` | Materiales de una asignatura (metadatos y enlaces) |

Los materiales van como *resources* y no como tool porque son contenido navegable,
no una accion. Nunca se descarga el contenido de los ficheros: solo su nombre,
tamano, fecha y URL, y **solo de la asignatura que abras**.

El filtro `course` de las tools acepta el codigo (`14537`), el nombre o parte de el
(`vision`) o las siglas (`VC`).

## Instalacion

Necesitas [uv](https://docs.astral.sh/uv/) y Python 3.12+.

```bash
git clone https://github.com/pgomsan/upv-mcp.git
cd upv-mcp
uv sync
```

### 1. Consigue tu URL iCal

En la [intranet de la UPV](https://intranet.upv.es): **Horarios → Compartir
horarios**, y genera el enlace iCal de tu horario.

> Ese enlace lleva un token y **es equivalente a una contrasena**: cualquiera que lo
> tenga puede ver tu horario completo con tu nombre. No lo pegues en repositorios,
> issues ni capturas de pantalla.

### 2. Guardala en el llavero

```bash
uv run upv-mcp-config set schedule    # se pide sin eco, no se escribe en ningun fichero
uv run upv-mcp-config show            # comprobar (enmascarada)
```

Se guarda en el llavero del sistema (Keychain en macOS).

Si lo lanzas donde no hay terminal interactiva (un script, CI, o el `!` de Claude
Code), la URL se lee de stdin. Pasala desde un fichero para que no quede en el
historial del shell:

```bash
cat url.txt | uv run upv-mcp-config set schedule && rm url.txt
```

### 2b. Conecta PoliformaT (opcional)

Para ver tus entregas, materiales y avisos:

```bash
uv run upv-mcp-config set poliformat    # usuario y contrasena de la UPV
```

Van al llavero, igual que la URL. El servidor entra por el SSO de la UPV
(`cas.upv.es`) y lee la API REST oficial de Sakai: **solo lectura, solo tu cuenta**,
con un limite de 0.5 peticiones por segundo.

> Si te equivocas de contrasena, el servidor **no reintenta**: te lo dice y para.
> Reintentar contra el SSO de la universidad bloquea la cuenta.

Sin esto el servidor funciona igual, pero solo con el horario.

### 3. Registra el servidor en tu cliente

**Claude Desktop** — edita `claude_desktop_config.json`:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "upv": {
      "command": "/Users/TU_USUARIO/.local/bin/uv",
      "args": ["--directory", "/ruta/absoluta/a/upv-mcp", "run", "upv-mcp"]
    }
  }
}
```

> **Usa la ruta absoluta de `uv`** (`which uv` te la dice). Las apps con interfaz
> grafica de macOS no heredan el `PATH` de tu terminal, asi que poner `"uv"` a secas
> hace que el servidor no arranque y sin mensaje de error visible. Es el fallo mas
> comun al instalar esto.

Reinicia Claude Desktop **por completo** (`Cmd+Q`, no solo cerrar la ventana): el
fichero de configuracion solo se lee al arrancar. Deberias ver las tres tools.

Si no aparecen, el log esta en `~/Library/Logs/Claude/mcp-server-upv.log`.

**Claude Code**:

```bash
claude mcp add upv -- uv --directory /ruta/absoluta/a/upv-mcp run upv-mcp
```

## Limitaciones

**No ve tus examenes.** El calendario de horarios de la UPV contiene clases pero no
examenes, y en PoliformaT ni todo examen es una tarea ni toda tarea es un examen:
hay tareas tituladas «Examen parcial» que no lo son, y examenes que no aparecen como
tarea. Clasificarlos por el titulo produciria falsos positivos y negativos, asi que
**no se clasifican**.

El servidor declara esta limitacion en cada respuesta (`meta.coverage_note`) y las
descripciones de las tools instruyen al modelo para que diga *«no puedo ver tus
examenes»* y nunca *«no tienes examenes»*. Es deliberado: una respuesta falsa y
creible es peor que una incompleta.

Si encuentras un iCal de examenes en tu intranet, conectalo y funciona sin tocar
codigo:

```bash
uv run upv-mcp-config set exams
```

**Solo el curso academico en marcha.** PoliformaT acumula todas las asignaturas que
has cursado; se sincronizan unicamente las del ultimo curso, para no llenar las
respuestas de ruido. Para consultar entregas ya pasadas, `list_upcoming_deadlines`
acepta `days_back`.

**El estado de entrega puede ser desconocido.** Cada entrega dice si esta
`submitted`, `not_submitted` o `unknown`, y si ya esta corregida trae la nota y el
comentario del profesor. Las que llegan del calendario (y no de la herramienta de
Tareas) no tienen estado. El filtro `pending_only` deja fuera lo desconocido a
proposito: decirte que te falta algo que quiza ya entregaste es peor que callarse.

## Desarrollo

```bash
uv run pytest -q         # 122 tests
uv run mypy              # strict, limpio
uv run ruff check .
```

Inspeccionar el servidor a mano:

```bash
npx @modelcontextprotocol/inspector uv run upv-mcp
```

### Arquitectura

```
sources/   red + parseo -> modelos          (no importa mcp)
cache/     SQLite, migraciones versionadas  (no importa mcp)
tools/     filtrado y forma de la respuesta (no importa mcp, httpx2 ni icalendar)
server.py  registro de tools y resources    (unico fichero que toca el SDK)
```

La separacion se puso a prueba al integrar PoliformaT: entregas, materiales y
avisos entraron **sin tocar una linea de `tools/`** para las tools que ya existian.
`tests/test_architecture.py` verifica los invariantes analizando los imports con
`ast`, asi que una violacion rompe la build.

Decisiones razonadas en [`PLAN.md`](PLAN.md); convenciones para contribuir en
[`CLAUDE.md`](CLAUDE.md).

Los cambios se validan con el guion de [`docs/manual-testing.md`](docs/manual-testing.md):
20 casos que cubren ambiguedad entre tools, fechas relativas, rangos vacios,
truncado y preguntas que no deben disparar ninguna tool.

## Privacidad

- Tu `.ics` y tu base de datos local nunca se commitean (`.gitignore`).
- La URL iCal vive en el llavero del sistema, no en un fichero.
- El unico trafico de red es la descarga de tu propio calendario desde `upv.es`.

## Licencia

MIT
