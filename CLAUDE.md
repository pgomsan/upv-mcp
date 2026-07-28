# upv-mcp

Servidor MCP local que expone el calendario academico de la UPV. SDK `mcp` 2.x
(la clase es `MCPServer`; en la 1.x se llamaba `FastMCP`). Transporte stdio.

## Comandos

```bash
uv sync                  # instalar
uv run pytest -q         # tests
uv run mypy              # strict, debe salir limpio
uv run ruff check .      # lint (ruff format . para formatear)
uv run upv-mcp           # arrancar el servidor (stdio)
uv run upv-mcp-config show   # ver que URL iCal estan configuradas
```

Todo lo anterior debe pasar antes de dar por terminado un cambio.

## Invariantes de arquitectura

Se cumplen hoy. Romperlos es el unico motivo real para rechazar un PR aqui.

- `sources/` no importa `mcp`. Solo red + parseo -> modelos de dominio.
- `tools/` no importa `mcp`, ni `httpx2`, ni `icalendar`. Solo modelos + `repository`.
- `server.py` es el UNICO fichero que importa `mcp`.
- El objetivo: que PoliformaT entre en la v1 sin tocar `tools/`.

## Datos

- El `.ics` real del usuario NO se commitea (`.gitignore`). Los fixtures de
  `tests/fixtures/` son anonimizados y conservan la estructura exacta del generador
  de la UPV.
- Las URL iCal llevan un token en la propia URL: son credenciales, van al llavero
  del sistema via `upv-mcp-config`. Nunca a un fichero ni a un `.env` commiteado.
- Todo datetime se normaliza a Europe/Madrid **al parsear**, nunca en `tools/`.
- Peculiaridades del formato UPV: ver el docstring de `sources/ics.py`. Leelo antes
  de tocar el parser; no estan documentadas en ningun sitio publico.

## Convenciones de tools

- Nombre en `snake_case`, verbo primero: `get_schedule`, `list_upcoming_deadlines`.
- Una tool por modulo en `tools/`, registrada en `server.py`.
- La descripcion se trata como codigo de produccion, no como documentacion: es lo
  que decide si el modelo elige bien. Debe decir que devuelve, cuando usarla, y
  **cuando NO usarla redirigiendo por nombre** a la tool correcta.
- Devolver siempre datos estructurados y compactos: filtrar, ordenar y truncar antes
  de responder. Al truncar, `meta.truncated = true`.
- Si una tool no puede ver cierta informacion, decirlo en `meta.coverage_note`. Es lo
  que impide que el modelo afirme "no tienes examenes" cuando no los esta mirando.

## Norma para tools nuevas

Toda tool nueva llega con:
1. sus tests en `tests/test_tools.py` (incluido el caso vacio y el truncado),
2. su caso en `docs/manual-testing.md`, incluida una pregunta que NO deba dispararla.

El procedimiento completo esta en `.claude/skills/add-mcp-tool/SKILL.md`.

## Estado

v0: solo el `.ics` de horarios, sin scraping ni login.
`list_upcoming_deadlines` devuelve vacio a proposito (ver `PLAN.md`).
