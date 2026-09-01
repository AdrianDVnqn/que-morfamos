# Arquitectura del motor de búsqueda

Cómo funciona el RAG de Qué Morfamos **hoy**. Es un corte transversal, no una historia: describe el
estado actual y se actualiza; no crece.

> **Este documento y `DEV_LOG.md` son cosas distintas y conviene que sigan siéndolo.** El DEV_LOG es
> un diario —qué pasó, en qué orden, por qué se decidió cada cosa— y su valor es el rastro de
> decisiones, incluidos los callejones sin salida. Este archivo responde otra pregunta: *"si veo un
> síntoma raro, ¿en qué eslabón cae?"*. Mezclarlos fue lo que volvió inusable la información: para
> saber cómo funcionaba el motor había que leer 700 líneas en orden y aplicar mentalmente cada
> parche encima del anterior.

Los números de línea son del **01-sep-2026** y se van a mover. Los nombres de función, no.

---

## El recorrido completo, de un vistazo

```
consulta del usuario
      │
      ▼
[1] ROUTER LLM            analizar_query_semantica()      main.py:2017
      │                   → intención, keywords, sinónimos, zona
      ▼
[2] ATOMIZACIÓN           atomizar_keywords()             main.py:276
      │                   → parte "pizza sin tacc" en dos conceptos
      ▼
[3] ¿NOMBRÓ UN LUGAR?     detectar_mencion_exacta()       main.py:2113
      │   sí ──────────────────────────────────► camino SPECIFIC (resumen del lugar)
      │   no
      ▼
[4] EXPANSIÓN             variantes_de_concepto()         main.py:226
      │                   expandir_sinonimos()            main.py:321
      ▼
[5] ¿MODO GENÉRICO?       KEYWORD_TO_CATEGORIES           main.py:602
      │   sí → candidatos por categoría oficial de Google
      │   no → vector search + Hybrid Injection
      ▼
[6] FILTRO DE OCASIÓN     filtrar_no_presenciales()       main.py:84
      ▼
[7] RANKING EN CASCADA    procesar_recomendacion_pesado() main.py:2937
      │                   → (conceptos, evidencia, popularidad)
      ▼
[8] JUEZ LLM              verificar_candidatos_con_llm()  main.py:2750
      ▼
[9] ARMADO DE CARDS       exactos[:3] + relacionados[:2]
      ▼
respuesta + tarjetas
```

---

## 1. Router

`analizar_query_semantica()` (`main.py:2017`) manda la consulta a un LLM y recibe `intencion`,
`tipo`, `target_name`, `keywords`, `synonyms` y `donde`.

**La trampa:** `intencion` **no mapea 1 a 1** al `mode` que sale en la respuesta.

| intención | mode que devuelve la API |
|---|---|
| `RECOMMENDATION` | `rag` |
| `SPECIFIC` | `resumen` |
| `FOLLOWUP` | `general` — no emite modo propio, queda el default del wrapper |
| `STATS` | `estadisticas` |
| `BLOCK` con contexto fresco | `general` (un regaño y suma un strike) |
| `BLOCK` con `strikes >= 5` | `blocked` |

Ese desfasaje sorprende a cualquiera que espere simetría, y es por lo que el golden dataset guarda
`expected_intent` **y** `expected_mode` por separado.

## 2. Atomización de keywords

`atomizar_keywords()` (`main.py:276`) es una **red de seguridad sobre el router**, no una
optimización. El prompt ya le pide un término por concepto, con ejemplos explícitos, y aun así
devuelve la consulta pegada: medido, 5 de cada 6 consultas multiconcepto volvían como un solo blob
(`'pizza sin tacc'` → `['pizza sin tacc']`).

Eso rompía todo lo que viene después, porque **el blob no matchea nada**: los cuatro casos medidos
daban 0 lugares con el texto completo, mientras que descompuestos daban 19 lugares que son pizza *y*
sin tacc. Con todos los candidatos empatados en 0 conceptos, el orden caía íntegro a popularidad.

Rescata primero los conceptos conocidos de varias palabras (para no partir `"sin tacc"` ni
`"pet friendly"`), tolera plurales, y recién después parte por conectores. `"de"` queda **fuera** de
los conectores a propósito: partiría `"café de especialidad"` y `"casa de comidas"`.

## 3. Resolución de nombre propio

`detectar_mencion_exacta()` (`main.py:2113`) decide si el usuario nombró un lugar. Tres pasos, en
este orden y por buenas razones:

1. **Igualdad exacta normalizada.** Va primera. Antes vivía en un segundo loop inalcanzable, y por
   eso `"el tío"` resolvía a `"PERNILES Del Tío Rudy"` sin que el match exacto se evaluara nunca.
2. **El nombre completo dentro de la consulta** (`"qué tal es el tío"` → `El Tío`). Acá gana el
   nombre **más largo**: es lo que hace ganar a `827 Punto de Encuentro` sobre `Encuentro`.
3. **La consulta dentro del nombre** (`"growler"` → `Growler Bar`). Acá gana el **más corto**, que
   es el match más ajustado. El orden es el opuesto al del paso 2 y eso es deliberado.

Todo con **límites de palabra**: con substring desnudo, `"tio"` matcheaba `pa-TIO`, `PATIO JUEZ` y
hasta `Bakery and Confec-TIO-nery Curri` — 11 candidatos donde hay 2.

## 4. Expansión de términos

Dos funciones que parecen lo mismo y **usan vocabularios distintos a propósito**:

| función | vocabulario | por qué |
|---|---|---|
| `variantes_de_concepto()` (`:226`) | curado **+ generado** | contar cobertura de conceptos quiere vocabulario **amplio**: cuantas más formas de nombrar el concepto se reconozcan, mejor se detecta que un lugar lo cumple |
| `expandir_sinonimos()` (`:321`) | **sólo curado** | genera los términos de búsqueda que alimentan la Hybrid Injection y la consulta de reseñas: acá un sinónimo flojo mete candidatos que no corresponden |

No es una inconsistencia: usar el vocabulario generado en los dos lados subía la evidencia de 0.50 a
0.55 pero bajaba Recall de 0.83 a 0.79.

El vocabulario generado sale de `generar_vocabulario_conceptos.py`, que lo deriva del corpus real y
se regenera con cada scrape. Lo curado (`SINONIMOS_CURADOS`, `main.py:152`) **siempre pisa** a lo
generado.

## 5. La bifurcación grande: Modo Genérico vs RAG

Es **el punto de decisión menos evidente de todo el sistema**.

```python
es_query_generica = len(categorias_detectadas) > 0 and len(keywords_extra) == 0
```

Si todas las keywords están en `KEYWORD_TO_CATEGORIES` (`main.py:602`), la consulta se resuelve por
**categoría oficial de Google** y se saltea el vector search. `"mejores pizzas"` cae acá;
`"pizza sin tacc"` no, porque `"sin tacc"` no es una categoría.

**En Modo Genérico el orden es por popularidad, no por la cascada de conceptos.** Se intentó
cambiarlo y **el benchmark lo rechazó**: Recall bajó de 0.83 a 0.78. La razón es que ahí la única
keyword suele ser la categoría misma, así que todos empatan en `conceptos` y el desempate cae en
`evidencia`, que premia resúmenes verbosos. Está documentado en el código para que no se reintente.

## 6. Filtro de ocasión

`filtrar_no_presenciales()` (`main.py:84`) saca los locales de sólo mostrador **cuando la consulta
pide un lugar donde estar** (`cita`, `cenar`, `festejar`, `con amigos`…).

No es un filtro global, y eso importa: una panadería es la respuesta **correcta** para
"dónde desayunar" y una heladería para "helado vegano". Por eso `CATEGORIAS_SOLO_MOSTRADOR` deja
afuera a propósito panadería, cafetería, heladería y pastelería.

Nunca deja la lista vacía: `categoria` es un dato de Google que a veces miente, así que no puede ser
la causa de un "no encontré nada".

## 7. Conciencia de negación

`_mencion_positiva()` (`main.py:390`) es la que decide si un resumen **afirma** una característica.
Todo match por substring sobre texto de reseñas tiene que pasar por acá: *"lo único malo es que NO
tienen parrilla"* contiene el término y significa lo contrario.

Importa más de lo que parece, porque los resúmenes v1 de producción enumeran características para
casi todos los lugares — **la mayoría para decir que no las tienen**:

```
termino           aparece   POSITIVAS
pelotero            574        66  (11%)
estacionamiento     747       153  (20%)
vegano              293        58  (19%)
```

**Ojo con `FEATURES_CON_SIN` (`main.py:61`):** el `"sin"` es parte del nombre en *sin gluten*, *sin
TACC*, *sin lactosa*, pero es una negación en *sin pelotero* o *sin wifi*. Tratar todos igual hacía
que un resumen diciendo que el lugar **no** tiene algo contara como que **sí** lo tiene.

> **Lección de método:** medir ocurrencia cruda cuando el código usa un matcher con conciencia de
> negación da conclusiones al revés. Pasó dos veces; las dos hubo que corregir el DEV_LOG.

## 8. El ranking en cascada

`relevancia()`, dentro de `procesar_recomendacion_pesado()` (`main.py:2937`), ordena por una tupla:

```
(conceptos cubiertos, fuerza de evidencia, popularidad)
```

1. **`conceptos`** — cuántas keywords distintas cubre. Para "parrilla con pelotero", un lugar que
   cubre AMBAS le gana a uno más popular que sólo cubre una.
2. **`evidencia`** — cuántos términos del filtro menciona. Desempata cuando todos cubren el mismo
   concepto.
3. **`calc_score`** — popularidad, como último recurso.

**La distinción que hace que esto funcione:** `evidencia` significa cosas opuestas según el concepto.

- `"parrilla"` es un **tipo de comida**: un resumen que dice "parrilla, asado, parrillada" no
  describe un lugar más apto, sólo uno descrito con más palabras. Usar `evidencia` ahí bajaba el
  MRR de 1.00 a 0.33.
- `"sin tacc"` es un **requisito excluyente**: un resumen que dice "sin tacc, sin gluten, apto
  celíaco" **sí** describe un lugar que contempla la necesidad. Lucciana matchea 4 términos y
  Antares 1, y esa diferencia es real.

`CONCEPTOS_EXCLUYENTES` (`main.py:181`) es lo que separa los dos casos, vía
`es_concepto_excluyente()` (`main.py:249`).

## 9. El Juez

`verificar_candidatos_con_llm()` (`main.py:2750`) valida que el local **ofrezca** lo que se pidió.
Sólo rechaza por categoría totalmente errónea, requisito excluyente incumplido o cierre — nunca por
calidad, servicio o higiene: eso lo decide el usuario.

`candidatos_confiables` permite **saltear el Juez** para los que vinieron de una fuente ya validada
(categoría oficial). Lo que entró por Hybrid Injection (texto libre) **siempre** pasa por el Juez,
porque `categoria` suele ser demasiado genérica para conceptos que son característica del menú y no
tipo de negocio: BIO ZEN figura como "Restaurant" en Google pese a ser vegetariano.

## 10. Armado de cards

`exactos[:3] + relacionados[:2]` — **un techo duro de 5**. Vale saberlo antes de interpretar
cualquier métrica: un caso con 8 lugares esperados tiene recall máximo 0.62 aunque acierte los 5.

Para consultas de un solo concepto se conserva el orden en que el Juez devolvió los aprobados, salvo
que el concepto sea excluyente (ver §8).

## 11. Detalle de una card (`/restaurant`)

Dos fases, para que la tarjeta se sienta inmediata: `solo_base=1` devuelve metadata y reseñas en
~1.6s, y el análisis del LLM llega aparte a los ~6s.

- `get_keywords_from_topic()` (`main.py:1624`) — filtra las genéricas.
- `rankear_reviews_por_topico()` (`main.py:1676`) — relevancia binaria, sin bonus por rating.
- `obtener_reviews_por_local(terminos=…)` (`main.py:915`) — **pasarle los términos es lo que hace
  que funcione**: sin eso traía las 15 más recientes y para Ohana ninguna de las 15 hablaba del tema
  buscado, contra 15 de 15 pasándolos.

## 12. Caché

`RedisCacheManager` (`main.py:456`), sobre Upstash. TTL 7 días.

| namespace | qué guarda | clave |
|---|---|---|
| `analysis_v93` | salida del router | consulta normalizada |
| `vsearch` | búsqueda vectorial | consulta normalizada |
| `juez_v1` | aprobados del Juez | **hash del prompt** |
| `respuesta_rag_v1` | texto generado | **hash del prompt** |
| `desc` | descripción de card | nombre + tópico + tono |
| `detail_full_v4` / `detail_topic_v4` | detalle de card | nombre + tópico (+ tono) |

**Por qué los dos nuevos se clavan al hash del prompt y no a la consulta:** el prompt ya lleva
adentro el tono, la consulta y los locales concretos con su evidencia, así que si cambia cualquiera
de esas cosas la clave cambia sola. Eso les da **invalidación automática** cuando el scraper
actualiza resúmenes o ratings — algo que los namespaces viejos, clavados al nombre, no tienen.

El fallback del Juez (cuando falla el JSON y devuelve todos los candidatos) **no se cachea a
propósito**: fijaría el resultado de un fallo transitorio para todas las consultas siguientes.

---

## Cómo se mide todo esto

`python run_benchmark.py` sobre `golden_dataset.json` (33 casos). Hay **dos scoreboards separados**,
y confundirlos lleva a conclusiones falsas:

- **Por nombre** (`Recall@5`, `Precision@5`, `MRR`) — para consultas con ground truth de nombres.
- **Por evidencia** — para consultas de concepto: en vez de *"¿devolvió estos nombres?"* pregunta
  *"¿lo que devolvió cumple lo que se pidió?"*, verificando contra el resumen de cada lugar con el
  mismo `_mencion_positiva` del backend.

El segundo existe porque una lista de nombres es el instrumento equivocado para consultas de
concepto, y se comprobó **en los dos sentidos**: con frases contiguas el dataset dejaba afuera
lugares que sí corresponden, y con co-ocurrencia laxa metía una heladería en "cervecería con patio".

Línea base al 01-sep-2026:

```
27/33 | Recall@5=0.80 | Precision@5=0.84 | MRR=0.97
Evidencia: 25/47 lugares devueltos cumplen los conceptos pedidos (0.53)
```

**El número a mover es el de evidencia.** Y el cuello de botella ya no es el ranking: los resúmenes
se comen entre un tercio y la mitad de las características que las reseñas confirman, y el resumen
es la única evidencia que el ranking lee. Eso es el pendiente #1 del DEV_LOG.
