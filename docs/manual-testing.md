# Guion de prueba manual

La v0 no lleva evals automaticas: la validacion es manual en Claude Desktop con una
suscripcion iCal real. Este guion existe para que esa prueba sea **reproducible** y
no dependa de acordarse de que probar.

Recorrelo entero antes de publicar un cambio que toque tools, descripciones o
modelos de salida. Anota el resultado; si un caso falla, casi siempre se arregla en
la descripcion de la tool, no en el codigo.

## Preparacion

```bash
uv run upv-mcp-config set schedule     # pega tu URL iCal, no se muestra por pantalla
uv run upv-mcp-config show             # confirma que esta guardada
```

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

El bloque mas importante de la v0, porque aqui es donde el modelo puede mentir de
forma creible.

| # | Pregunta | Comportamiento correcto |
|---|---|---|
| 12 | "¿tengo alguna entrega esta semana?" | Debe decir que **no puede verlas** (no hay PoliformaT ni calendario de examenes). **Mal**: "no tienes ninguna entrega". |
| 13 | "¿tengo examenes en junio?" | Igual: declara la limitacion, no niega su existencia. |
| 14 | "dame todo mi horario del curso" | Trunca a 50 y **lo dice**, sugiriendo acotar. **Mal**: presentarlo como completo. |

## Bloque 5 — Vacio y bordes

| # | Pregunta | Comportamiento correcto |
|---|---|---|
| 15 | "¿que clases tengo el 15 de agosto?" | "No hay clases ese dia" con naturalidad. No lo trata como error ni reintenta. |
| 16 | "¿que tengo del 20 al 10 de octubre?" | Rango invertido: error legible, y deberia preguntar si queria decir al reves. |

## Bloque 6 — Nada deberia dispararse

| # | Pregunta | Comportamiento correcto |
|---|---|---|
| 17 | "¿que nota tengo en Estadistica?" | **Ninguna tool.** Explica que solo tiene acceso al calendario. |
| 18 | "explicame el teorema de Bayes" | **Ninguna tool.** Responde de su conocimiento. |
| 19 | "¿quien da Sistemas Operativos?" | Zona gris: aceptable `get_schedule` (el docente esta en los datos). Lo que no vale es inventarse el nombre. |

## Bloque 7 — Degradacion

| # | Escenario | Comportamiento correcto |
|---|---|---|
| 20 | Sin red (modo avion), preguntar el horario | Responde desde cache. `meta.stale` es true y deberia mencionar que los datos podrian no estar al dia. |

---

## Plantilla de resultados

```
Fecha:            Version:            SDK mcp:
Bloque 1  __/4    Bloque 2  __/4    Bloque 3  __/3
Bloque 4  __/3    Bloque 5  __/2    Bloque 6  __/3    Bloque 7  __/1

Fallos y correccion aplicada:
-
```
