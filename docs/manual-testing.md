# Guion de prueba manual

La validacion es manual, en Claude Desktop con datos reales. Este guion existe para
que esa prueba sea **reproducible** y no dependa de acordarse de que probar.

Se puede pasar a mano o con subagentes de contexto limpio: ver
[`pruebas-con-agentes.md`](pruebas-con-agentes.md), que trae los prompts listos.

Recorrelo entero antes de publicar un cambio que toque tools, descripciones o
modelos de salida. Anota el resultado; si un caso falla, casi siempre se arregla en
la descripcion de la tool, no en el codigo.

## Preparacion

```bash
uv run upv-mcp-config set schedule     # pega tu URL iCal, no se muestra por pantalla
uv run upv-mcp-config set poliformat   # usuario y contrasena UPV (opcional)
uv run upv-mcp-config show             # confirma que esta guardado
```

Hazlo en una terminal de verdad. Sin TTY la URL se lee de stdin
(`cat url.txt | uv run upv-mcp-config set schedule`).

Registra el servidor en Claude Desktop (ver README) y reinicia la aplicacion.

## Que se comprueba en cada caso

Para cada pregunta, tres cosas:

1. **Seleccion de tool**: ¿llamo a la que toca? (Se ve desplegando la llamada.)
2. **Validez de la salida**: ¿los datos son correctos y estan dentro de lo pedido?
3. **Fidelidad de la respuesta**: ¿lo que le dice al usuario coincide con lo que
   devolvio la tool, sin inventar ni omitir avisos?

---

## Bloque 1 — Seleccion basica

| # | Pregunta | Tool esperada | Que verificar |
|---|---|---|---|
| 1 | "¿cual es mi proxima clase?" | `get_next_class` | Da asignatura, aula y hora. No pasa fechas. |
| 2 | "¿que clases tengo el 10 de septiembre?" | `get_schedule` | `start_date` = `end_date` = 2026-09-10. |
| 3 | "¿que examenes tengo pronto?" | `list_upcoming_deadlines` | Ver bloque 4. |
| 4 | "¿donde tengo que ir ahora?" | `get_next_class` | Menciona aula **y** edificio. |

## Bloque 2 — Ambiguedad entre tools

El fallo tipico es confundir estas dos. Son los casos que mas valor tienen.

| # | Pregunta | Tool esperada | Fallo que se busca |
|---|---|---|---|
| 5 | "¿a que hora empiezo manana?" | `get_schedule` | Que use `get_next_class` porque suena a inmediato. |
| 6 | "¿que me queda hoy?" | `get_schedule` (hoy-hoy) | Que devuelva solo la siguiente y se deje el resto. |
| 7 | "¿tengo clase ahora mismo?" | `get_next_class` | Que pida un rango innecesariamente. |
| 8 | "¿cuando es mi siguiente examen?" | `list_upcoming_deadlines` | Que lo busque en `get_schedule`. |

## Bloque 3 — Fechas relativas

Las resuelve el modelo, no el servidor. Se comprueba que el rango calculado sea el
correcto, y `meta.generated_at` da la fecha del servidor para contrastar.

| # | Pregunta | Que verificar |
|---|---|---|
| 9 | "¿que tengo la semana que viene?" | Lunes a domingo de la semana siguiente, no 7 dias a partir de hoy. |
| 10 | "¿y el jueves?" (tras la anterior) | Mantiene el contexto: el jueves de esa semana. |
| 11 | "¿que clases tengo este mes?" | Del dia 1 al ultimo del mes en curso. |

## Bloque 4 — Cobertura y honestidad

El bloque mas importante, porque aqui es donde el modelo puede mentir de forma
creible.

| # | Pregunta | Comportamiento correcto |
|---|---|---|
| 12 | "¿tengo alguna entrega esta semana?" | Con PoliformaT conectado, las lista. Si no hay, dice que no hay **entregas proximas**, no que no exista la informacion. |
| 13 | "¿tengo examenes en junio?" | Debe decir que **no puede ver los examenes** y por que. **Mal**: "no tienes examenes", y **mal** tambien colar una tarea titulada "Examen" como si fuera el examen oficial. |
| 14 | "dame todo mi horario del curso" | Trunca a 50 y **lo dice**, sugiriendo acotar. **Mal**: presentarlo como completo. |
| 15 | "dame todos los materiales de Vision 3D" | El listado viene recortado a 60; debe decirlo y no fingir que estan todos. |

## Bloque 5 — PoliformaT

Requiere `upv-mcp-config set poliformat`.

| # | Pregunta | Tool esperada | Que verificar |
|---|---|---|---|
| 16 | "¿que entregas tengo pendientes?" | `list_upcoming_deadlines` | `days_back` **no** se usa: solo lo que queda. |
| 16b | "¿que me queda por entregar del curso?" | `list_upcoming_deadlines` con `pending_only` | Solo lo no entregado. **Mal**: colar como pendiente algo de estado `unknown`. |
| 16c | "¿me han corregido la practica 3?" | `list_upcoming_deadlines` | Debe leer `graded` y dar la nota con su escala ("8,30 sobre 10,00"), no la nota suelta. |
| 17 | "¿que entregue en Vision por Computador?" | `list_upcoming_deadlines` con `days_back` | Aqui SI debe mirar atras. Es el caso que justifica el parametro. |
| 18 | "¿ha dicho algo el profe de Robotica Movil?" | `list_announcements` | Trae autor y fecha. No confundir con deadlines. |
| 19 | "¿donde estan los apuntes de Redes Industriales?" | **Resource**, no tool | Debe leer `upv://materiales/14541`, no llamar a ninguna tool. |
| 20 | "¿que asignaturas tienen material?" | **Resource** `upv://materiales` | Lista de asignaturas con su URI. No debe tardar: no descarga nada. |
| 20b | "¿que entregas tengo de Vision por Computador?" | `list_upcoming_deadlines` con `course` | Debe usar el filtro, no pedir todo y descartar. |
| 20c | "¿que clases tengo de Redes esta semana?" | `get_schedule` con `course` | Igual. Con un nombre inventado debe decir que esa asignatura no es suya. |
| 20d | "resumeme el caso de practicas de Interfaces" | `read_material` | Descarga el PDF y resume su contenido real, no el nombre del fichero. |
| 20e | "abre el zip de practicas de IHM" | `read_material` | Debe decir que un ZIP no lo puede leer y dar la URL. **Mal**: inventarse que contiene. |

## Bloque 6 — Vacio y bordes

| # | Pregunta | Comportamiento correcto |
|---|---|---|
| 21 | "¿que clases tengo el 15 de agosto?" | "No hay clases ese dia" con naturalidad. No lo trata como error ni reintenta. |
| 22 | "¿que tengo del 20 al 10 de octubre?" | Rango invertido: error legible, y deberia preguntar si queria decir al reves. |

## Bloque 7 — Nada deberia dispararse

| # | Pregunta | Comportamiento correcto |
|---|---|---|
| 23 | "¿que nota tengo en Estadistica?" | **Ninguna tool.** Las notas no se exponen. |
| 24 | "explicame el teorema de Bayes" | **Ninguna tool.** Responde de su conocimiento. |
| 25 | "¿quien da Sistemas Operativos?" | Zona gris: aceptable `get_schedule` (el docente esta en los datos). Lo que no vale es inventarse el nombre. |

## Bloque 8 — Degradacion

| # | Escenario | Comportamiento correcto |
|---|---|---|
| 26 | Sin red (modo avion), preguntar el horario | Responde desde cache. `meta.stale` es true y deberia mencionar que los datos podrian no estar al dia. |
| 27 | Contrasena de PoliformaT incorrecta | Error claro, **sin reintentos**, y el horario del `.ics` sigue funcionando. |

---

## Plantilla de resultados

```
Fecha:            Version:            SDK mcp:
Bloque 1  __/4    Bloque 2  __/4    Bloque 3  __/3    Bloque 4  __/4
Bloque 5  __/5    Bloque 6  __/2    Bloque 7  __/3    Bloque 8  __/2

Fallos y correccion aplicada:
-
```
