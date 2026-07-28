# upv-mcp

Servidor [MCP](https://modelcontextprotocol.io) local que expone tu calendario
academico de la **UPV** a cualquier cliente MCP: Claude Desktop, Claude Code o
Cursor. Preguntale por tu horario en lenguaje natural y responde con tus datos
reales.

> «¿cual es mi proxima clase?»
> → *Modelado y Control de Robots, lunes 7 de septiembre a las 15:00, AULA 1E 0.3
> (Edificio 1E).*

Se alimenta del export iCal de tu horario. **No hace scraping, no pide tu clave de
la UPV y no manda tus datos a ningun sitio**: todo ocurre en tu ordenador.

## Tools

| Tool | Para que sirve |
|---|---|
| `get_schedule(start_date, end_date)` | Clases y examenes en un rango de fechas |
| `get_next_class()` | La siguiente sesion desde ahora, con aula y hora |
| `list_upcoming_deadlines(days_ahead)` | Fechas limite proximas ([ver limitacion](#limitaciones-de-la-v0)) |

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

### 3. Registra el servidor en tu cliente

**Claude Desktop** — edita `claude_desktop_config.json`:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "upv": {
      "command": "uv",
      "args": ["--directory", "/ruta/absoluta/a/upv-mcp", "run", "upv-mcp"]
    }
  }
}
```

Reinicia Claude Desktop. Deberias ver las tres tools disponibles.

**Claude Code**:

```bash
claude mcp add upv -- uv --directory /ruta/absoluta/a/upv-mcp run upv-mcp
```

## Limitaciones de la v0

Esta version lee **solo el calendario de horarios**, que contiene clases pero **no
examenes**. Las entregas viven en PoliformaT, que aun no esta integrado.

Por eso `list_upcoming_deadlines` puede devolver una lista vacia. El servidor avisa
de esta limitacion en cada respuesta (`meta.coverage_note`) y las descripciones de
las tools instruyen al modelo para que diga *«no puedo ver tus entregas»* y nunca
*«no tienes entregas»*. Es deliberado: una respuesta falsa y creible es peor que una
respuesta incompleta.

Si tu intranet te deja generar un iCal que incluya examenes, conectalo y la tool
empieza a funcionar sin tocar codigo:

```bash
uv run upv-mcp-config set exams
```

## Desarrollo

```bash
uv run pytest -q         # 60 tests
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
server.py  registro de tools                (unico fichero que toca el SDK)
```

El objetivo de la separacion es que anadir PoliformaT en la v1 no obligue a tocar la
capa de tools. Decisiones razonadas en [`PLAN.md`](PLAN.md); convenciones para
contribuir en [`CLAUDE.md`](CLAUDE.md).

Los cambios se validan con el guion de [`docs/manual-testing.md`](docs/manual-testing.md):
20 casos que cubren ambiguedad entre tools, fechas relativas, rangos vacios,
truncado y preguntas que no deben disparar ninguna tool.

## Privacidad

- Tu `.ics` y tu base de datos local nunca se commitean (`.gitignore`).
- La URL iCal vive en el llavero del sistema, no en un fichero.
- El unico trafico de red es la descarga de tu propio calendario desde `upv.es`.

## Licencia

MIT
