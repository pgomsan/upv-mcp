# Referencia: escribir la descripcion de una tool

Detalle de apoyo para `SKILL.md`. Leelo solo si estas redactando o corrigiendo la
descripcion de una tool.

## Por que esto importa mas que el codigo

El modelo no lee la implementacion. Elige la tool leyendo su nombre, su descripcion
y su esquema, y en un `tools/list` las tres descripciones aparecen juntas. Si dos se
solapan, el modelo elige mal de forma consistente, y el sintoma se parece a un bug
de datos ("me dice que no tengo clase el jueves") cuando en realidad llamo a
`get_next_class` en vez de a `get_schedule`.

## Plantilla

```
Devuelve <QUE, con los campos concretos que trae>.

USALA cuando <situacion>:
- "<pregunta literal del usuario>"
- "<otra pregunta literal>"

NO LA USES:
- Si <situacion vecina> -> usa <nombre_exacto_de_la_otra_tool>. <Por que esta no
  sirve para eso>.
- Si <otra situacion> -> usa <nombre_exacto>.

<FORMATO de los parametros: fechas absolutas YYYY-MM-DD, rangos inclusivos, etc.>

<QUE SIGNIFICA UNA RESPUESTA VACIA y que hacer con meta.truncated.>
```

## Reglas aprendidas en este repo

**Usa preguntas literales, no categorias.** "Preguntas sobre el horario" no ayuda.
`"que clases tengo el martes"` si, porque se parece a lo que el usuario escribira.

**El `NO LA USES` nombra la alternativa.** Decir "no la uses para lo inmediato" deja
al modelo sin salida. "usa get_next_class" la da.

**Di lo que la tool ignora.** `get_next_class` ignora cualquier fecha que le pases.
Si no se dice, el modelo asume que puede pasarle "el jueves" y la respuesta sale mal
sin ningun error visible.

**Distingue vacio de fallo.** Sin esto, el modelo interpreta una lista vacia como
error y se pone a reintentar o a disculparse. Di explicitamente que vacio significa
"no hay nada en ese rango".

**Declara lo que NO puedes ver.** Es la regla mas importante del repo.
`list_upcoming_deadlines` devuelve vacio en la v0 porque no hay fuente de examenes ni
entregas. Sin aviso, el modelo responde "no tienes nada pendiente": una frase falsa,
util y creible, que es la peor combinacion posible. Por eso su descripcion dice
literalmente que hay que responder "no puedo verlas" y nunca "no tienes ninguna", y
`meta.coverage_note` lo repite en cada respuesta.

**Vigila la longitud.** Las descripciones viajan en cada `tools/list`. Un test falla
por encima de 2000 caracteres. Si no cabe, sobra prosa, no contenido.

## Comprobacion rapida

Lee las descripciones de las tools que puedan confundirse, seguidas y sin mirar el
codigo, y responde: dada la pregunta X del usuario, ¿cual elegirias? Si dudas tu,
el modelo tambien.
