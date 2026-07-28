---
name: add-mcp-tool
description: Anadir una tool MCP nueva al servidor upv-mcp, o modificar la descripcion, los parametros o el modelo de salida de una existente. Usala cuando la tarea implique exponer una capacidad nueva a los clientes MCP de este repo (por ejemplo "anade una tool para buscar aulas libres" o "haz que get_schedule filtre por asignatura"). No la uses para cambios en sources/, cache/ o el parser .ics, que no tocan la superficie MCP.
---

# Anadir una tool a upv-mcp

Una tool nueva son cinco ficheros. Sigue el orden: el modelo de salida primero, la
descripcion al final (es lo que mas se reescribe).

## 1. Modelo de salida en `src/upv_mcp/models.py`

Un modelo pydantic con `Field(description=...)` en cada campo: el SDK los convierte
en el output schema que ve el cliente.

Envuelvelo en un `...Result` que incluya `meta: ResultMeta`. No es opcional: `meta`
lleva `truncated`, `stale` y `coverage_note`, que son lo que evita que el modelo
presente datos recortados o incompletos como si fueran la verdad completa.

## 2. Consulta en `src/upv_mcp/cache/db.py`

Filtra **en SQL**, no en Python. Anade el metodo `..._between(...)` y su
`count_..._between(...)` hermano: hacen falta los dos, porque el contador es lo que
permite reportar el truncado con honestidad.

Si cambias el esquema: edita `schema.sql`, sube `SCHEMA_VERSION` y anade la rama de
migracion en `migrate()`.

## 3. Modulo en `src/upv_mcp/tools/<nombre>.py`

Una tool por modulo. Exporta dos cosas: `DESCRIPTION` (constante) y una funcion
async que recibe `repo: AcademicRepository` primero y `limit: int` como keyword.

```python
async def mi_tool(repo: AcademicRepository, param: str, *, limit: int) -> MiResult:
    if <parametro invalido>:
        raise ValueError("mensaje que el usuario final pueda entender")
    await repo.ensure_fresh()
    total = repo.cache.count_...(...)
    items = repo.cache....(..., limit=limit)
    return MiResult(items=items, meta=build_meta(repo, total_matching=total, returned=len(items)))
```

Prohibido en este fichero: importar `mcp`, `httpx2` o `icalendar`. Si crees que los
necesitas, el trabajo va en `sources/`, no aqui.

## 4. Registro en `src/upv_mcp/server.py`

Unico sitio que toca el SDK. El `Context` se recibe por parametro (en el SDK 2.x no
existe `get_context()`):

```python
@mcp.tool(title="Titulo corto", description=mi_tool_module.DESCRIPTION)
async def mi_tool(param: str, ctx: Context[AppContext]) -> MiResult:
    app = ctx.request_context.lifespan_context
    return await mi_tool_module.mi_tool(app.repo, param, limit=app.settings.max_results)
```

Nombre en `snake_case` con el verbo delante: `get_`, `list_`, `find_`.

## 5. La descripcion

Es la parte que decide si el modelo acierta, y la que hay que escribir con mas
cuidado. Estructura obligatoria en este repo:

1. Que devuelve exactamente, con los campos relevantes.
2. `USALA` + preguntas literales de ejemplo.
3. `NO LA USES` + para cada caso, **el nombre de la tool correcta** y por que.
4. Formato de los parametros (fechas absolutas `YYYY-MM-DD`, etc.).
5. Que significa una respuesta vacia, y que hacer si `meta.truncated` es true.

Regla: si dos tools pueden confundirse, **ambas** descripciones deben mencionar a la
otra. Los tests lo comprueban.

Detalle y ejemplos completos: `reference.md` en esta misma carpeta.

## 6. Tests y prueba manual (no son opcionales)

En `tests/test_tools.py`, como minimo:

- caso normal,
- **rango o filtro vacio** -> debe devolver lista vacia, no error,
- **truncado** -> `limit` bajo, comprobar `meta.truncated is True` y que
  `total_matching` sigue siendo el total real,
- parametros invalidos -> `ValueError` con mensaje claro.

En `tests/test_server.py` la tool entra sola en las comprobaciones de registro y de
esquema, pero anadela a la parametrizacion de desambiguacion de descripciones.

En `docs/manual-testing.md`, anade su bloque: al menos una pregunta que SI debe
dispararla y una que NO (tipicamente la que deberia ir a la tool vecina).

## Antes de terminar

```bash
uv run pytest -q && uv run mypy && uv run ruff check .
```

Y arranca el servidor de verdad para ver la tool en `tools/list`:

```bash
npx @modelcontextprotocol/inspector uv run upv-mcp
```
