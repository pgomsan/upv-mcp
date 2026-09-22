# upv-mcp

Servidor MCP local con el calendario academico de la UPV y PoliformaT. SDK `mcp`
2.x (la clase es `MCPServer`; en la 1.x era `FastMCP`). Transporte stdio.

## Comandos

```bash
uv sync                  # instalar
uv run pytest -q         # tests
uv run mypy              # strict, debe salir limpio
uv run ruff check .      # lint (ruff format . para formatear)
uv run upv-mcp           # arrancar el servidor (stdio)
uv run upv-mcp-config show   # ver que credenciales estan configuradas
```

Los cuatro primeros deben pasar antes de dar por terminado un cambio.

## Invariantes de arquitectura

Romperlos es el unico motivo real para rechazar un PR aqui.

- `sources/` no importa `mcp`. Solo red + parseo -> modelos de dominio.
- `tools/` no importa `mcp`, ni `httpx2`, ni `icalendar`. Solo modelos + `repository`.
- `server.py` es el UNICO fichero que importa `mcp`.
- `tests/test_architecture.py` lo comprueba con `ast`: rompe la build si se viola.

## Datos

- El `.ics` real no se commitea. Los fixtures de `tests/fixtures/` son anonimizados
  y conservan la estructura exacta del generador de la UPV.
- Credenciales (URL iCal con token, usuario y clave UPV) van al llavero via
  `upv-mcp-config`. Nunca a un fichero ni a un `.env`.
- Todo datetime se normaliza a Europe/Madrid **al parsear**, nunca en `tools/`.
- Peculiaridades de cada origen: docstrings de `sources/ics.py`, `poliformat.py` y
  `cas.py`. Leelos antes de tocarlos; no estan documentadas en ningun sitio publico.
- PoliformaT NO permite login por API: solo CAS. Un login rechazado no se reintenta
  **nunca**, porque bloquea la cuenta.
- Los examenes vienen de su propio iCal (opcional). Nunca los deduzcas del titulo
  de una tarea: hay tareas llamadas "Examen" que no lo son, y al reves.

## Convenciones de tools

- Nombre en `snake_case`, verbo primero: `get_schedule`, `list_upcoming_deadlines`.
- Una tool por modulo en `tools/`, registrada en `server.py`.
- Navegar contenido va como **resource**; descargarlo y convertirlo, como tool.
- La descripcion es codigo de produccion, no documentacion: decide si el modelo
  elige bien. Di que devuelve, cuando usarla y **cuando NO, redirigiendo por
  nombre** a la tool correcta.
- Devolver datos estructurados y compactos: filtrar, ordenar y truncar antes de
  responder. Al truncar, `meta.truncated = true`.
- Si una tool no puede ver algo, decirlo en `meta.coverage_note`. Es lo que impide
  que el modelo afirme "no tienes examenes" cuando no los esta mirando.

## Norma para tools nuevas

Cada tool nueva llega con sus tests (incluidos el caso vacio y el truncado) y su
caso en `docs/manual-testing.md`, con una pregunta que NO deba dispararla.
Procedimiento completo: `.claude/skills/add-mcp-tool/SKILL.md`.

## Estado

v1 completa: horarios y examenes por `.ics`, y PoliformaT via la API REST de Sakai.
Sin huecos de cobertura declarados (ver `PLAN.md`).

Hay un segundo entrypoint ademas del servidor MCP: `upv-publish` (`publish.py`), que sube el .ics de entregas al Worker de `worker/`.
`horario.ics` es ENTRADA y no se toca nunca; `build/entregas.ics` es SALIDA.
El publicador nunca escribe en la raiz del repo: solo en `build/`.
