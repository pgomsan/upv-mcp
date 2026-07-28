# PLAN.md — decisiones de diseno y roadmap

Estado: **v1 completa**. Cuatro tools y dos resources sobre dos fuentes: el export
`.ics` del calendario UPV y PoliformaT, este ultimo via la API REST oficial de
Sakai. Sin scraping en ningun punto.

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

### PoliformaT se lee por API REST, no por scraping

PoliformaT expone `/direct/`, la **API REST oficial de Sakai (EntityBroker)**, con
JSON y 74 entity providers. Descubrirlo cambio el plan entero: la v1 estaba pensada
como scraping de HTML y acabo siendo un cliente REST, mas estable y mucho mejor
ciudadania.

El login nativo de Sakai (`POST /direct/session`) esta capado: devuelve 403 incluso
con usuarios inexistentes, asi que no es un problema de credenciales. La unica via
es reproducir el flujo CAS de `cas.upv.es`.

De ahi la regla que no se negocia: **un login rechazado no se reintenta jamas**.
Reintentar contra el SSO de la universidad bloquea la cuenta. Solo los fallos de red
son reintentables, y hay un test que falla si eso cambia.

### Los examenes no se adivinan

Ninguna de las dos fuentes cubre los examenes. El `.ics` de horarios trae solo
clases (verificado: 841 de 841), y en PoliformaT **ni todo examen es una tarea ni
toda tarea es un examen**: hay tareas tituladas "Examen parcial de laboratorio" y
examenes que no aparecen como tarea.

Se decidio no clasificar por titulo. Una heuristica asi produce falsos positivos y
negativos, y aqui una clasificacion que miente es peor que no clasificar. Todo lo
que devuelve `list_upcoming_deadlines` son entregas, y `meta.coverage_note` declara
que los examenes no se ven.

Es la misma regla que gobierna el resto del repo: el problema no es la respuesta
incompleta, sino que el modelo concluya "no tienes examenes", que es una frase falsa,
util y creible.

### Mirar atras es opcional y esta apagado por defecto

`list_upcoming_deadlines` acepta `days_back`, pero su valor por defecto es 0: la
pregunta habitual es sobre lo que queda por hacer. Incluir el pasado sin que lo pidan
llena la respuesta de entregas cerradas y entierra lo relevante. La descripcion dice
explicitamente que solo se use ante una pregunta sobre algo ya pasado.

### El estado de entrega tiene tres valores, no dos

Cada entrega dice `submitted`, `not_submitted` o **`unknown`**. El tercero no es un
adorno: 6 de 82 tareas reales no traen registro de entrega, y las fechas limite que
vienen del calendario en vez de la herramienta de Tareas tampoco lo tienen.

Tratarlas como no entregadas seria inventar, y del peor modo posible: haciendo que
el estudiante crea que le falta algo que ya hizo. Por eso `pending_only` deja fuera
lo desconocido, y `meta.coverage_note` dice cuantas hay en ese estado.

**Trampa de la API, verificada y no supuesta**: el campo `submitted` de Sakai vale
`true` en las 76 tareas con submission, incluidas las que la propia interfaz muestra
como "No ha empezado". Fiarse de su nombre habria dicho al estudiante que lo tiene
todo entregado. El campo correcto es `userSubmission`, que casa exactamente con la
fecha de entrega (38 y 38). Hay un test de regresion.

### Los materiales son resources, no una tool

Un PDF de apuntes es contenido navegable que el cliente decide cuando leer, no una
accion que ejecutar. Van como MCP resources: `upv://materiales` y
`upv://materiales/{codigo}`.

Nunca se descarga el contenido de los ficheros, solo metadatos y URL. Una sola
asignatura del usuario tiene 1159 recursos; el listado se trunca a 60 por seccion y
lo declara en el propio texto.

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

## Que valido la arquitectura

Anadir PoliformaT entero -- entregas, materiales y anuncios -- **no obligo a tocar
`tools/` ni una linea** para las tres tools que ya existian. `PoliformatSource`
produce los mismos modelos que ya consumian. Era la apuesta de la v0 y salio bien.

## Roadmap

### v1.1 — Calendario de examenes (bloqueado por datos)

Es el unico hueco declarado que queda. Los examenes tienen un calendario propio en
algun sitio de la UPV, todavia por localizar. Dos vias posibles:

1. Un segundo iCal desde el visor de horarios de la intranet. El codigo ya lo
   soporta: `IcsSource` acepta N calendarios, `_classify()` distingue examen de
   clase, y hay fixture y tests que lo cubren. Seria configuracion, no codigo.
2. Alguna pagina o servicio de la ETSINF con el calendario oficial de examenes.

Al conectarlo, el aviso de cobertura desaparece solo, igual que desaparecio el de
PoliformaT al integrarlo.

### v1.2 — Mejoras sobre lo ya construido

- **Refresco selectivo**: PoliformaT tarda ~1 peticion por asignatura para los
  materiales. Cachear por asignatura con TTL propio evitaria rehacerlo entero.
- **Filtrar por asignatura** en `get_schedule` y `list_upcoming_deadlines`.

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
