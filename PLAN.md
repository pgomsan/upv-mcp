# PLAN.md — decisiones de diseno y roadmap

Estado: **v0 completa**. Tres tools sobre el export `.ics` del calendario UPV,
sin scraping, sin credenciales de login.

---

## Decisiones tomadas, y por que

### SDK `mcp` 2.x en vez de 1.x

La spec MCP `2026-07-28` y el SDK `mcp` 2.0.0 salieron el mismo dia en que se
escribio la v0. En la 2.x `FastMCP` pasa a llamarse `MCPServer`, el `Context` se
recibe por inyeccion de parametro y los campos son `snake_case`.

Se eligio la 2.x porque **la 1.x quedo en mantenimiento (solo parches de seguridad)
ese mismo dia**, y nacer sobre una API ya marcada como legacy no tenia sentido. El
riesgo (un SDK con horas de vida) se mitigo verificando la API contra el codigo
fuente instalado en vez de contra documentacion, y comprobando por stdio que la
negociacion hacia atras funciona: un cliente que pide `2025-11-25` es atendido sin
problema, que es la ruta que usan hoy Claude Desktop y Cursor.

Consecuencia: el SDK 2.x depende de `httpx2`, asi que el proyecto usa `httpx2` y no
`httpx`, para no arrastrar dos stacks HTTP.

### La arquitectura en tres capas es el punto del repo

`sources/` no importa `mcp`; `tools/` no importa `httpx2` ni `icalendar`; solo
`server.py` toca el SDK. No es purismo: es lo que hace que PoliformaT (v1) entre
tocando unicamente `sources/`. Los invariantes estan en `CLAUDE.md` porque son la
unica regla cuyo incumplimiento justifica rechazar un cambio.

`repository.py` no estaba en el diseno inicial. Se anadio al ver que la decision de
*cuando* refrescar no encajaba ni en `sources/` (que no debe conocer la cache) ni en
`tools/` (que no debe conocer la red).

### Las URL iCal son credenciales

El enlace iCal de la UPV lleva un token de 96 caracteres en la propia URL: quien la
tiene, lee el horario completo con el nombre del alumno. Por eso van al llavero del
sistema (Keychain) via `upv-mcp-config`, y no a un `.env`. La variable de entorno
existe solo como fallback para tests y CI.

Adelantar el `keyring` a la v0 (estaba previsto para la v1) sale gratis y elimina la
via mas probable de filtracion: un `cat .env` en una demo o un screenshot.

### La cache reemplaza, no fusiona

`replace_calendar()` borra y reinserta el calendario entero. Si a una clase le
cambian el aula o la quitan del horario, un merge la dejaria viva para siempre. Los
841 eventos se reparsean en ~100 ms, asi que no hay razon para optimizar esto.

### Degradar antes que fallar

Si la descarga falla pero hay cache, se sirve la cache con `meta.stale = true`. Solo
se propaga el error si ademas la cache esta vacia. Un servidor local que revienta
porque no hay wifi es inutil justo cuando mas falta hace (yendo a clase).

### `list_upcoming_deadlines` se expone aunque devuelva vacio

**El calendario "Horario de Clases" de la UPV no contiene ni un solo examen** (se
verifico: 841 de 841 eventos son clases), y las entregas viven en PoliformaT, que es
v1. La tool no tiene hoy ninguna fuente de datos.

Se mantiene porque el problema real no es la lista vacia, sino lo que el modelo
concluye de ella: "no tienes nada pendiente" es una frase falsa, util y creible. Por
eso su descripcion le ordena responder *"no puedo verlas"* y nunca *"no tienes
ninguna"*, y `meta.coverage_note` repite la limitacion en cada respuesta.

El soporte para un segundo calendario de examenes esta implementado y con tests:
conectarlo sera configuracion (`upv-mcp-config set exams`), no codigo.

### Sin evals automaticas en la v0

Se descartaron a peticion: la validacion es manual en Claude Desktop. En su lugar
queda `docs/manual-testing.md`, un guion de 20 casos reproducible que cubre lo mismo
que habrian cubierto los evals (ambiguedad entre tools, fechas relativas, rangos
vacios, truncado, y preguntas que no deben disparar nada).

Lo que si esta automatizado es la parte determinista: los tests comprueban que cada
descripcion incluye su bloque `NO LA USES` y **nombra a las otras dos tools**, de
modo que una regresion en la desambiguacion rompe la build.

### Sin LangChain ni LangGraph

Este servidor no orquesta nada: expone capacidades. Un framework de agentes aqui
seria peso muerto.

---

## Roadmap

### v0.1 — Calendario de examenes (bloqueado por datos)

Falta unicamente el enlace iCal de examenes desde el visor de horarios de la
intranet. El codigo ya lo soporta: `IcsSource` acepta N calendarios, `_classify()`
distingue examen de clase, y hay fixture y tests que lo cubren.

Al conectarlo, `list_upcoming_deadlines` empieza a devolver datos y su
`coverage_note` se reduce sola.

### v1 — PoliformaT (Sakai)

La parte fragil, y el motivo de que la v0 haya validado antes la forma del servidor.

- **Autenticacion**: login UPV, credenciales siempre desde el llavero.
- **Entregas**: herramienta "Tareas" -> modelos `Assignment` ya existentes.
- **Materiales**: herramienta "Recursos" -> `Material`, expuestos como **MCP
  resources**, no como tool. Un PDF de apuntes es contenido navegable que el cliente
  decide cuando leer, no una accion que ejecutar.
- **Buena ciudadania**: `RateLimiter` conservador (ya escrito, `<= 1 req/s`),
  User-Agent identificable, acceso unicamente a datos de la propia cuenta, respeto a
  robots.txt y cache agresiva. Sakai es infraestructura compartida de la universidad.
- Solo debe tocarse `sources/`. Si hay que modificar `tools/`, la arquitectura
  fallo y hay que revisar el cambio.

### v2 — Reserva de salas

Requiere escritura, no solo lectura, y es un salto cualitativo: una tool que actua
sobre el mundo necesita confirmacion explicita del usuario, idempotencia y una via
de cancelacion. Antes de implementarla hay que decidir como se anota una tool
destructiva o con efectos, via `ToolAnnotations` del SDK.

### Trabajo transversal pendiente

- CI en GitHub Actions con `pytest` + `mypy --strict` + `ruff` (el fallback por
  variable de entorno ya permite correr sin llavero).
- Publicar en PyPI para que otros estudiantes instalen con `uvx upv-mcp`.
- Subagentes para trabajo en paralelo (uno escribiendo la tool, otro las pruebas).
  El terreno esta preparado: las capas estan separadas y `add-mcp-tool` documenta el
  procedimiento. No se crearon en la v0 a proposito.
