# 📔 Bitácora de Desarrollo - Que Morfamos

Este archivo registra los hitos técnicos, decisiones de diseño y correcciones importantes realizadas durante el desarrollo del proyecto.

## 📅 Sesión: 15 de Enero de 2026 (Madrugada épica)

### 🚀 Hitos Alcanzados

#### 1. Implementación de Hybrid RAG (Retrieval-Augmented Generation)
- **Problema:** El vector search puro fallaba en encontrar platos específicos ("milanesas", "sushi") si los embeddings semánticos diluían la importancia de la palabra clave exacta.
- **Solución:** Se implementó un sistema híbrido en `main.py`.
    - **Vector Search:** Recupera candidatos por similitud semántica.
    - **Keyword Frequency Injection:** Se escanea el texto de las reviews en la base de datos buscando la keyword exacta (ej: "milanesa") y se inyectan los restaurantes con mayor frecuencia de menciones en el contexto del LLM.
- **Resultado:** Recall mejorado significativamente para platos específicos. "Milanesas" ahora recupera "El Boliche de Alberto" y "Club de la Milanesa" aunque el vector search fallara.

#### 2. Suite de Benchmarking Automatizada
- **Implementación:** Se creó `run_benchmark.py` y `benchmark_cases.json`.
- **Funcionalidad:**
    - Define casos de prueba ("bares en el rio", "pelotero", "sushi").
    - Calcula un **Ground Truth (GT)** teórico consultando directamente la base de datos con filtros deterministas (SQL/Pandas).
    - Compara la respuesta del RAG contra el GT para medir **Recall** y **Precisión**.
    - Permite iterar modificaciones en el backend midiendo el impacto real en calidad.

#### 3. Juez LLM Explicativo
- **Mejora:** El LLM encargado de filtrar candidatos ("Juez") ahora retorna un JSON estructurado con listas de `aprobados` y `rechazados`, incluyendo una **razón de rechazo** para cada uno.
- **Impacto:** Permite debuggear por qué ciertos lugares válidos eran descartados (ej: "Evidencia vaga", "Solo menciones negativas").

#### 4. Corrección de Enrutamiento (Router Fixes)
- **Problema:** Consultas como "lugares *para* chicos" o "opciones *sin* tacc" eran malinterpretadas como búsquedas de nombres propios ("Para", "Sin") debido a la búsqueda difusa.
- **Solución:** Se implementaron **Blocklists** y **Stopwords** mejoradas en `detectar_mencion_exacta`. Palabras comunes ya no activan la búsqueda por nombre específico.

#### 5. Infraestructura y Monitoreo
- **Comparativa LLM:** Se habilitó soporte para **DeepSeek** vs **OpenAI** mediante variable de entorno `AI_PROVIDER`. (Benchmark: DeepSeek tiene calidad similar pero mayor latencia).
- **IP Geolocation:** 
    - Se corrigió la URL de la API `theipapi.com` que estaba mal formada.
    - **Hotfix Prod:** Se agregó `import urllib.request` faltante que causaba crash en producción (`NameError`).
- **Dashboard Legacy:** Se modificó el scraper `monitor_reviews.py` para escribir también en la tabla `scraping_logs`, reviviendo el gráfico de actividad histórico que había dejado de funcionar el 10/01.

#### 6. Despliegue a Producción
- **Acción:** Deploy manual a **Fly.io** (`fly deploy`) para reflejar todos estos cambios en el entorno productivo, corrigiendo la versión obsoleta del 8 de Enero.

#### 7. Detección de Zona Mejorada (Post-Deploy Debugging)
- **Problema:** El LLM no detectaba consistentemente la zona en queries como "bares en el rio" (retornaba `None`), causando que el filtro de zona no se aplicara.
- **Solución Dual:**
    - **Prompt Mejorado:** Se agregaron ejemplos explícitos al prompt de `analizar_query_semantica` ("bares en el rio" → `donde: "rio"`).
    - **Regex Fallback:** Si el LLM falla, un fallback por regex detecta patrones como "en el X", "zona X", "cerca del X".
- **Cache Busting:** Se invalidó el caché de Upstash (bump de `v84` → `v86`) para forzar regeneración de análisis.

#### 8. Juez LLM: Separación de Responsabilidades
- **Problema:** El Juez rechazaba lugares válidos (ej: "Cerveza Patagonia") porque buscaba evidencia de UBICACIÓN ("río") en las reviews, cuando eso ya lo filtra el filtro de zona.
- **Solución:**
    - El Juez ahora recibe una **query limpia** sin ubicación ("bares" en vez de "bares en el rio").
    - El prompt del Juez incluye instrucción explícita: "IGNORA LA UBICACIÓN, solo evalúa el TIPO de lugar".

#### 9. Discord Logging Mejorado
- **Cambios:**
    - La **query del usuario** ahora aparece PRIMERO y destacada.
    - Se muestra la **zona detectada** ("🗺️ Zona: Rio") o "Todo Neuquén" si no hay filtro.
- **Arquitectura Limpia:** La `zona_detectada` fluye desde `procesar_consulta_gen` → evento `meta` → endpoint `chat_stream` → `log_user_query_to_discord`. Sin duplicación de lógica.

#### 10. Fix de Filtro de Zona: Word Boundaries y Restricción de Campos
- **Problema:** El filtro de zona "rio" daba falsos positivos con:
    1. Substrings: Direcciones como "Pe**rio**distas Neuquinos" (Solucionado con Word Boundaries `\b`).
    2. Nombres de calles: Lugares en zona Oeste con nombres de calle como "Rio Desaguadero".
- **Solución Final:** 
    - Se implementó búsqueda por **Regex con Word Boundaries**.
    - Se **eliminó el campo 'direccion'** del buscador de zona. Ahora el sistema solo mira las columnas `zona` y `barrio` para filtrar geográficamente.
- **Resultado:** Precisión total. Solo matchea si el área comercial o el barrio pertenecen efectivamente a la zona buscada.

#### 11. Modo Genérico (Fast Path por Categoría)
- **Problema:** Búsquedas como "bares", "pizzerías" o "sushi" disparaban un proceso pesado de Vector Search + Juez LLM que podía rechazar lugares válidos por una sola reseña negativa.
- **Solución:**
    - Se creó un mapeo de **Keywords a Categorías oficiales** de Google Places.
    - **Fast Path:** Si la query es genérica, se filtra directamente por la columna `categoria` en el DataFrame.
    - **Bypass de Juez:** Se elude el Juez LLM para estas búsquedas, confiando en la clasificación oficial y el ranking por rating/popularidad.
- **Resultado:** Respuesta instantánea, mayor recall y fin de los rechazos absurdos para lugares populares como "Cervecería Owe".

#### 12. Enriquecimiento de Contexto con Resúmenes IA
- **Mejora:** El sistema ahora carga y utiliza la columna `resumen_reviews` (generada previamente por IA con sampleo inteligente) para alimentar el contexto del RAG final.
- **Impacto:** Las descripciones generadas por el chat son mucho más precisas y representativas de la experiencia general del lugar, en lugar de basarse en snippets aleatorios de las últimas 5 reseñas.

#### 13. Optimización de Memoria (Anti-OOM)
- **Problema:** Al agregar los resúmenes de IA al JOIN de la base de datos, el uso de RAM se disparó porque el resumen (pesado) se repetía en cada una de las 50.000 filas de reseñas, causando un crash por Out of Memory en Fly.io.
- **Solución:** Se desacoplaron los datos en dos DataFrames globales: `df` (reseñas ligeras para búsqueda) y `df_lugares` (metadatos únicos por local). El resumen se carga una sola vez por restaurante y se accede solo al armar la respuesta final.

#### 14. Fix Geográfico Global (El "Caso Crafter")
- **Problema:** Lugares como "Crafter" o "Antares" desaparecieron de los resultados. Se detectó que el filtro inicial de Neuquén era demasiado estricto, aceptando solo códigos postales Q8300/1/2. Lugares importantes con direcciones incompletas en Google Maps (ej: "Neuquén, Argentina") estaban siendo descartados preventivamente.
- **Solución:** Se flexibilizó el filtro en `lifespan`. Ahora se aceptan lugares si el CP es correcto **O** si la dirección menciona "Neuquén" y no pertenece a ciudades vecinas (Cipolletti, Plottier, etc.).
- **Resultado:** Se recuperaron ~75 lugares legítimos que estaban "en las sombras".

#### 15. Arquitectura "Chad": Router Maestro Unificado (LLM)
- **Problema:** El sistema dependía de múltiples heurísticas manuales, `if/else` interminables, listas de palabras clave (`rec_keywords`) y búsquedas difusas (`RapidFuzz`) muy agresivas para decidir si una query era una recomendación o un local específico.
    - Ejemplo: "pasteleria en el rio" se confundía con un local llamado "Pastelería" e intentaba dar info de ese lugar, fallando.
- **Solución:**
    - Se unificó `analizar_query_semantica` y el ruteador en una sola llamada al LLM Inteligente.
    - **Router Maestro:** Ahora el LLM decide la `intencion` (RECOMMENDATION, SPECIFIC, FOLLOWUP, STATS, BLOCK) y extrae el `target_name` solo si es realmente un lugar.
    - Se eliminaron ~200 líneas de heurísticas manuales frágiles por un flujo limpio y declarativo.
- **Resultado:** El sistema entiende el contexto. Sabe que "pastelería" es un producto y no un lugar, a menos que digas "Pastelería Najuian". Velocidad mantenida y código mucho más mantenible.


## 📅 Sesión: 6 de Marzo de 2026 (Optimización de Performance y Memoria)

### 🚀 Hitos Alcanzados

#### 1. Solución de Errores de Memoria (OOM)
- **Problema:** El servidor crasheaba en Fly.io (768MB RAM) al intentar procesar ~180k reseñas. El log indicaba errores al intentar alojar arrays de ~2.8 GiB.
- **Solución:**
    - Se eliminó el pre-proceso de `texto_ascii` y `restaurante_ascii` en el DataFrame global, ahorrando gigabytes de memoria efímera.
    - Se corrigió el manejo de `ArrowStringArray` (proveniente de SQLAlchemy en Fly.io) asegurando la conversión a Series de Pandas con el índice correcto para que `.fillna()` funcione sobre los ratings.

#### 2. Reducción de Latencia Pre-Stream (de ~20s a ~4s)
- **Problema:** El usuario reportaba esperas de 20 segundos antes de recibir el primer token de texto.
- **Soluciones:**
    - **Paralelización de Intent + Embeddings:** Se lanzó `analizar_query_semantica` y `vectorstore.similarity_search` en paralelo usando `asyncio.create_task`. Esto ahorra el tiempo de bloqueo del primer LLM (~2.5s).
    - **Caché de Vector Search (Redis):** Se implementó una capa de caché en Redis para las búsquedas vectoriales con un TTL de **7 días**. Búsquedas repetidas ahora evitan la llamada a la API de Embeddings de OpenAI (~6s).
    - **Async Compliance:** Se migró a `asimilarity_search` para evitar bloquear el event loop de FastAPI.

#### 3. Optimización de Carga de Tarjetas (UI Responsiva)
- **Mejora:** Se refactorizó `obtener_restaurant_cards` para procesar todas las descripciones en **una única llamada por lotes (batch)** al LLM, en lugar de llamadas secuenciales.
- **Fallback de Datos:** Se prioriza el uso de la columna `resumen_reviews` (ya existente en la base de datos) para mostrar descripciones rápidas sin depender de llamadas externas al LLM cuando no es estrictamente necesario.

#### 4. Logging de Producción y Debugging
- **Implementación:**
    - Se agregó `SERVER_VERSION` y timestamp de inicio en los logs de Fly.io.
    - El endpoint `/chat/stream` ahora loguea: IP del usuario, Query original, Tono, Zona detectada, Cantidad de resultados y Tiempo de respuesta total.
    - Se incluyó un preview de la respuesta del LLM en los logs para monitoreo rápido sin entrar a Discord.

#### 6. Arquitectura v7.0: Solución Definitiva de Latencia y Bloqueo
- **Problema:** El servidor bloqueaba el event loop por ~25s durante inyecciones y rankings pesados, causando fallos de health check (`servicecheck-00-http-8000 failed`) y timeouts.
- **Solución - Threading:** Se extrajeron las operaciones CPU-bound a la función `procesar_recomendacion_pesado` y se implementó `asyncio.to_thread`. El event loop ahora queda libre para responder pings mientras se procesa la recomendación.
- **Solución - Indexación O(1):** Se indexaron los DataFrames `df` y `df_lugares` por `restaurante`. 
    - Se eliminaron decenas de miles de escaneos lineales (`df["restaurante"] == x`) en bucles. 
    - Funciones como `obtener_coordenadas` y `aplicar_filtro_zona` ahora funcionan en tiempo constante.
- **Resultado:** El tiempo de preparación pre-stream bajó de **25s a <1s** en la mayoría de los casos. Estabilidad total en Fly.io.

#### 7. Arquitectura v7.1: Latencia Residual y Tarjetas
- **Pre-stream setup:** Reducido de 23s a <1s mediante el uso de lookups indexados en el Juez LLM y offloading a threads.
- **Generación de Tarjetas:** Reducida de 85s a <15s netos (tiempo del LLM) eliminando escaneos lineales redundantes.
- **Integridad:** Corregidos errores de estructura en `main.py`.

## 📅 Sesión: 23 de Agosto de 2026 (Selección de locales)

### 🐛 Corrección de selección por menú
- **Problema:** Al buscar un local con un nombre compartido, como `827 Punto de encuentro`, el sistema mostraba correctamente el menú de opciones, pero al elegir una opción fallaba con el error `'fecha'` y no devolvía el restaurante.
- **Causa:** La ruta de selección mezclaba el DataFrame de metadata (`df_lugares`) con el DataFrame de reseñas. El resumen intentaba ordenar metadata como si tuviera la columna `fecha`. Además, la detección podía devolver solo una palabra distintiva, como `Encuentro`, en lugar del nombre canónico completo.
- **Solución:**
    - Se priorizan coincidencias de nombre completo y se conserva el nombre canónico del registro.
    - `resumir_opiniones_local_gen` usa `df_lugares` para metadata y carga las reseñas bajo demanda desde `reviews`.
    - La ruta tolera locales sin reseñas sin intentar ordenar un DataFrame vacío o incompleto.
- **Validación:** Compilación con `python -m py_compile main.py` y prueba aislada con nombres ambiguos, devolviendo correctamente `827 Punto de encuentro`.

### 🗓️ Fecha visible de actualización del backend
- Se agregó `backend_updated_at` al endpoint `/health` para que el frontend pueda mostrar `Backend actualizado` con una fecha explícita.
- La fecha se inyecta automáticamente desde la fecha ISO del commit desplegado por GitHub Actions mediante un argumento de build de Docker.
- `/health` también devuelve `last_scraping`, calculado desde `MAX(fecha)` de `scraping_logs`.
- Las fechas sin hora se interpretan como calendario local para evitar que Argentina muestre el día anterior.

## 📅 Sesión: 24 de Agosto de 2026 (Golden Dataset de Evaluación RAG)

### 🎯 Objetivo
Reemplazar el benchmark de calidad existente (`benchmark_cases.json` + `run_benchmark.py`) por un **golden dataset** con ground truth curado a mano, métricas de retrieval estándar y un juez LLM opcional de calidad de respuesta, para poder medir el impacto real de futuras mejoras al agente RAG.

### 🐛 Hallazgo: el benchmark anterior ya no funcionaba
- **Problema:** `run_benchmark.py` validaba `main.df` antes de correr casos, pero desde la arquitectura **LAZY v8** `df` es siempre `None` (solo `df_lugares` se carga en memoria; las reviews se consultan bajo demanda). El benchmark hacía no-op en cada corrida sin que nadie lo notara.
- **Problema de fondo, no solo el bug anterior:** el ground truth se recalculaba con un `if/elif` por `case_id` que reimplementaba en pandas la lógica de filtrado del backend — comparaba una heurística contra otra heurística, no contra una verdad objetiva, y no escalaba.

### 🔧 Reescritura completa
- **`eval_metrics.py` (nuevo):** módulo puro con `recall_at_k`, `precision_at_k`, `mrr`, `intent_match` y un agregador con breakdown por intención.
- **`golden_dataset.json` (nuevo, reemplaza `benchmark_cases.json`):** 22 casos con `expected_restaurants` **curados a mano** corriendo cada query contra la Supabase real y revisando los resultados (no recalculados por heurística). Cobertura: zonas, categorías/keywords, nombre exacto único, nombre ambiguo (regresión "827 Punto de Encuentro"), nombre parcial informal, typos/sin acentos, follow-up con contexto, estadísticas, bloqueo (con y sin contexto fresco), bypass de palabras prohibidas, queja legítima con lenguaje fuerte (no debe bloquearse en falso), y edge case sin resultados esperados.
- **`run_benchmark.py` (reescrito):** sin heurísticas pandas; carga el ground truth directo del JSON, corre cada caso contra `/chat` vía `TestClient`, calcula métricas con `eval_metrics.py`. Mantiene compatibilidad con `BENCHMARK_MODULE`/`AI_PROVIDER` (usados por `benchmark_vs.ps1`). Suma un juez LLM opcional (`WITH_JUDGE=1`) que evalúa relevancia, fidelidad/alucinación y tono de la respuesta generada, reutilizando el estilo de prompt del Juez de candidatos existente.
- **`analyze_benchmark_logs.py`:** suma columnas de Recall@5/Precision@5/MRR/Intent Accuracy a la comparativa, sin romper el parseo del pass-rate existente.
- **Resultado:** **22/22 casos PASARON** corriendo contra la Supabase real de producción (`Recall@5=0.98 | Precision@5=0.85 | MRR=1.00 | Intent Accuracy=1.00`).

### 🐛 Bug real #1: `/chat` pasaba `df` (siempre `None`) en vez de `df_lugares`
- **Descubierto** al intentar correr el nuevo benchmark contra el endpoint `/chat`: cualquier camino que necesitara metadata de restaurantes (RECOMMENDATION, SPECIFIC_INFO) tiraba `AttributeError: 'NoneType' object has no attribute 'index'`.
- **Causa:** el handler de `/chat` (no-streaming) le pasaba la variable global `df` (arquitectura LAZY, siempre `None`) a `procesar_consulta`, mientras que `/chat/stream` sí usaba `df_lugares` correctamente. Nadie lo notaba en producción porque el frontend solo usa `/chat/stream`.
- **Solución:** se corrigió el call site para pasar `df_lugares`, igual que el endpoint de streaming.

### 🐛 Bug real #2: preguntas de seguimiento crasheaban con `KeyError: 'fecha'`
- **Descubierto** curando el caso `followup_detalle_entidad` ("¿tiene wifi?" tras seleccionar un local).
- **Causa:** `responder_followup_gen` recibía el parámetro `df` de `procesar_consulta_gen`, que en ambos endpoints es en realidad `df_lugares` (metadata, sin columna `fecha`/`texto` por reseña) y no el DataFrame de reviews que la función esperaba. Es la misma familia de bug que el fix de "827 Punto de Encuentro" de la sesión anterior, pero en una función distinta que no había sido migrada a la arquitectura LAZY. **Afectaba a `/chat/stream` en producción real**, no solo al benchmark.
- **Solución:** si el `df` recibido no trae la columna `fecha`, se cargan las reviews reales bajo demanda con `obtener_reviews_por_local` (mismo patrón ya usado en `resumir_opiniones_local_gen`).
- **Validación:** el caso `followup_detalle_entidad` pasó de fallar con 500 a `PASÓ` en el golden dataset, corriendo contra datos reales.

### 📊 Logging de queries reales (para minar el dataset a futuro)
- **Problema:** no existía ninguna fuente persistida de queries reales de usuarios — el logging de producción va solo a Discord (no queryable).
- **Solución:** se diseñó la tabla `query_logs` (`create_query_logs_table.py`, script idempotente `CREATE TABLE IF NOT EXISTS`, mismo patrón standalone que `migrate_data.py`) y se agregó `log_user_query_to_db` en `main.py`, llamado junto al logging existente de Discord en `/chat` y `/chat/stream`. Envuelto en try/except que solo loguea un warning — nunca puede tirar abajo el request, igual que el logging a Discord.

## 📅 Sesión: 24 de Agosto de 2026 (Re-curación del Golden Dataset + LangSmith)

### 🐛 El golden dataset validaba la calidad mediocre del RAG en vez de detectarla
- **Problema detectado por el usuario:** en varios casos de `golden_dataset.json` (ej. `parrilla en el centro`), `expected_restaurants` incluía lugares que claramente no correspondían a la categoría pedida (bodegones genéricos devueltos como "parrilla"). Causa raíz: la curación original había aceptado literalmente lo que devolvía el RAG como ground truth, sin verificar cada candidato — comparar el sistema contra sí mismo no detecta nada.
- **Solución:** se re-curaron 7 casos cruzando cada candidato contra el texto completo (no truncado) de `resumen_reviews`, buscando evidencia explícita positiva/negativa, en vez de confiar en la categoría o en el output del RAG. Se sacaron falsos positivos (`CASI RODRIGUEZ RESTAURANTE`, `Restaurante El Ciervo`, `Lotito's A lo Bestia`, `Pastas Caseras Elvira`, `10 ALMAS`, `Santo`) y se reconstruyó `feature_pelotero` y `keyword_vegano` buscando en **toda la base** (no solo lo que el RAG devolvía), lo que expuso gaps de recall reales: "827 Punto de Encuentro" tiene pelotero confirmado por reseñas pero el RAG no lo encuentra para esa query, y ninguno de los 5 lugares que el RAG sugiere para "opciones veganas" es realmente vegano.
- **Resultado:** el benchmark pasó de reportar (falsamente) `22/22 | Recall@5=0.98` a `20/22 | Recall@5=0.90 | Precision@5=0.72`, con las dos fallas nuevas señalando problemas reales del RAG a investigar, no ruido de curación.
- **Nota sobre `categoria`:** se investigó si la columna `categoria` (asignada por LLM según recordaba el usuario) podía ser la causa de estos fallos. Se confirmó leyendo `enrichment-validator.py` (repo scraper) que `categoria` se scrapea directo del DOM de Google Maps (`extraer_categoria_de_lugar`); el único uso de LLM ahí es un filtro binario "es gastronómico o no", nunca reescribe el valor. Las categorías en inglés ("Brewpub", "Beer hall") probablemente vienen de que Google sirve la UI en inglés en los runners de GitHub Actions (US) pese al flag `--lang=es-AR`. La causa más relevante que sí se confirmó: el prompt que genera `resumen_reviews` (`deepseek_utils.py:generar_resumen_reviews`) instruye deliberadamente evitar negaciones literales ("no tiene X" → "solo opciones con Y") para no confundir a los embeddings, lo que hace poco confiable cualquier detección de ausencia de una feature basada en keywords simples sobre ese texto.

### 🧪 Integración con LangSmith
- **Motivación:** el golden dataset corría solo local/logs — sin historial de corridas ni forma de comparar cambios en el tiempo. Se eligió LangSmith por integración mínima (el proyecto ya usa LangChain) y tier gratuito, en vez de armar un dashboard propio.
- **Implementación:**
    - `judge.py` (nuevo): se extrajo el juez de calidad de respuesta de `run_benchmark.py` a un módulo compartido, reutilizado también por LangSmith.
    - `run_langsmith_eval.py` (nuevo): sincroniza `golden_dataset.json` como Dataset de LangSmith (resync completo en cada corrida) y corre `evaluate()` con 4 evaluadores base (recall@5, precision@5, MRR, intent match) más un juez de calidad opcional (`WITH_JUDGE=1`, emite `judge_relevancia`/`judge_fidelidad_ok`/`judge_tono_ok` como feedback separado vía el formato multi-métrica `{"results": [...]}`).
    - Target configurable: local vía `TestClient` (default) o contra producción con `TARGET_URL=<url> python run_langsmith_eval.py`.
- **Verificado end-to-end** contra la cuenta real de LangSmith: dataset con 22 examples, experimentos corridos en modo local y contra producción (Fly.io), scores de feedback confirmados por API (`recall_evaluator`, `precision_evaluator`, `mrr_evaluator`, `intent_evaluator`, y las métricas del juez).
- **Ajuste de API:** la SDK instalada (`langsmith==0.10.11`) difiere de la documentación pública consultada — `create_examples` espera `examples=[{"inputs":..., "outputs":..., "metadata":...}]` en vez de listas separadas `inputs=[]`/`outputs=[]`, y un evaluador que devuelve múltiples métricas debe envolver la respuesta en `{"results": [{"key":..., "score":...}, ...]}` en vez de un dict plano. Corregido y validado corriendo contra la cuenta real, no solo por lectura de docs.

## 📅 Sesión: 25 de Agosto de 2026 (Fix de recall/precisión: negación, evidencia del Juez y taxonomía de categorías)

### 🎯 Objetivo
Arreglar las dos fallas reales del RAG que el golden dataset expuso la sesión anterior: `feature_pelotero` (Recall@5=0.14) y `keyword_vegano` (Recall@5=0.00, Precision@5=0.00).

### 🐛 Bug raíz #1: Hybrid Injection contaminaba Modo Genérico sin validación
- **Causa (confirmada con trazas de debug reales, no hipótesis):** para "opciones veganas", Modo Genérico detectaba correctamente 3 candidatos por `categoria` exacta, pero el paso "Hybrid Injection" (`procesar_recomendacion_pesado`, búsqueda por keyword sobre `resumen_reviews`) corría **incondicionalmente** e inflaba esos 3 candidatos genuinos a 10, mezclando lugares sin relación real. Como Modo Genérico activa `skip_juez=True` (asumiendo que el candidato ya viene validado por categoría), **ninguno de los 10 pasaba por ningún filtro**.
- **Causa de fondo, compartida con el bug de pelotero:** el matching por keyword sobre `resumen_reviews` (tanto en Hybrid Injection como en la clasificación de relevancia grupo_alta/grupo_baja) no distinguía menciones positivas de negativas — "no cuenta con pelotero" contaba igual que "tiene pelotero".
- **Fix:** nuevo helper `_mencion_positiva(texto, termino, ventana=70)` (`main.py`, cerca de `_normalizar_busqueda`) que rechaza una mención si hay una palabra de negación (`no|sin|carece|falta|ausencia`) en una ventana de 70 caracteres hacia atrás — mismo criterio validado a mano durante la re-curación del golden dataset. Se aplicó tanto en Hybrid Injection como en la clasificación grupo_alta/grupo_baja.

### 🐛 Bug raíz #2 (extensión, pedida por el usuario): Modo Genérico es demasiado ciego para conceptos que son "característica del menú", no "tipo de negocio"
- **Problema:** aun con Hybrid Injection apagado en Modo Genérico, "opciones veganas" seguía fallando porque `categoria` de Google para lugares como BIO ZEN es simplemente "Restaurant" (genérico) aunque su resumen diga literalmente "restaurante vegetariano y vegano" — la categoría de Google no siempre refleja bien estas características.
- **Fix (diseño acordado con el usuario):** Hybrid Injection ahora corre **siempre**, incluso en Modo Genérico, pero se distingue `candidatos_confiables` (los que vinieron de `categoria` exacta o vector search) de los agregados por texto. Los confiables se siguen aprobando directo (rápido); los agregados por texto pasan por el Juez LLM antes de mostrarse — mismo patrón que ya funcionaba bien para `pelotero`. `procesar_recomendacion_pesado` ahora retorna `(candidatos_a_verificar, grupo_alta, candidatos_confiables)`.
- **Bug adicional descubierto en el camino — evidencia ciega del Juez:** `obtener_evidencia_para_juez` le mandaba al Juez los primeros 700 caracteres fijos del resumen, sin importar dónde estuviera la mención real de la keyword. Como "vegano"/"sin TACC" suelen vivir en el párrafo 3 del resumen (después del carácter 700), el Juez evaluaba a ciegas y aprobaba por default ("ante la duda, aprobá"), colando lugares como una cervecería o una heladería. Fix: la evidencia ahora es una ventana de ±300 caracteres alrededor de la mención real de la keyword, con fallback al prefijo de 700 si no hay match.
- **Bug adicional — orden arbitrario al cortar candidatos inyectados:** Hybrid Injection cortaba a los primeros 10 matches en orden de tabla (esencialmente arbitrario), no por relevancia. "827 Punto de Encuentro" (pelotero) y "BIO ZEN" (vegano) quedaban fuera del corte pese a ser matches genuinos, tapados por lugares menos populares que aparecían antes en la tabla. Fix: los matches ahora se ordenan por `calc_score` (rating + popularidad) antes de cortar a 10.

### 📊 Categoría "Argentinian restaurant" demasiado amplia para "parrilla"
- **Problema:** con los fixes anteriores funcionando, `combo_parrilla_centro` seguía fallando: "CASI RODRIGUEZ RESTAURANTE" (un bodegón genérico sin mención de parrilla) se colaba porque su `categoria` real es "Argentinian restaurant", que estaba mapeada a "parrilla"/"asado" en `KEYWORD_TO_CATEGORIES` — y en Modo Genérico eso alcanza para aprobar sin pasar por el Juez.
- **Decisión (con el usuario):** sacar "Argentinian restaurant" del mapeo de "parrilla" y "asado" — queda solo con `Barbecue restaurant`, `Steak house`, `Grill`, `Chophouse restaurant`. Se evaluó también una versión más sofisticada (categorías "amplias" que activan Modo Genérico pero cuyos candidatos igual pasan por el Juez, mismo patrón que vegano/pelotero) pero se optó por la versión simple para no seguir sumando superficie de cambio en la misma sesión.
- **Resultado:** arregló de una tanto `combo_parrilla_centro` como `no_falso_bloqueo_queja` (el mismo problema de categoría afectaba a los dos).

### 📈 Resultado final del benchmark
`20/22 PASARON | Recall@5≈0.85-0.90 | Precision@5≈0.66-0.77` (varía levemente entre corridas por no-determinismo del LLM). Los dos casos que quedan en rojo (`plato_milanesas`, `keyword_vegano`) **no son bugs**: son curación desactualizada del golden dataset (ver abajo) y ruido esperable (nombres truncados por el LLM que no matchean el string exacto del ground truth, y el hallazgo de datos obsoletos de más abajo).

### 🔎 Hallazgo colateral: el fix de "ordenar por popularidad" expuso que la curación del golden dataset era demasiado angosta en más casos de los pensados
- Al mejorar el ranking de Hybrid Injection, el RAG empezó a encontrar candidatos genuinos y **más populares** (3000-4500 reseñas) que mi curación original no tenía: `plato_milanesas` ahora también encuentra `Restaurante Estación Q` (plato insignia "Milanesa del Bosque"), `El Tío`, `Cabildo Pizzería`, `Ache`; `keyword_vegano` encuentra `Franz y Peppone` (calzones veganos) y `Lucciano's Paseo de la Costa` (helados veganos). Se verificó cada uno leyendo el `resumen_reviews` completo antes de sumarlo a `expected_restaurants` — no se aceptó a ciegas, mismo estándar que la re-curación anterior.
- **Lección:** "ordenar por popularidad" no solo arregla el problema puntual de recall (encontrar un lugar tapado) — también revela que un ground truth curado a mano casi nunca es exhaustivo. El golden dataset necesita revisiones periódicas, no es un artefacto que se cura una vez y queda fijo.

### 🐛 Dato desactualizado: "Cuchí" cerró hace ~1 año
- El fix hizo que el RAG empezara a devolver "Cuchí" para "opciones veganas" (resumen confirma "fuerte identidad vegetariana y vegana", 313 reseñas) — pero el usuario confirmó que el local cerró hace aproximadamente un año. La base de datos no tiene ningún mecanismo de revalidación periódica de cierres (el scraper solo detecta "permanently closed" en el momento del scrapeo inicial, ver `enrichment-validator.py` del repo scraper). Se excluyó del ground truth del golden dataset, pero el RAG en producción **puede seguir recomendándolo** — queda como deuda técnica real para una sesión futura (posible: re-scrapear periódicamente el estado de "abierto/cerrado" de los lugares ya cargados).

### 🐛 Hallazgo sin resolver: extracción de keywords confunde el tema de la queja con el pedido real
- Para la query "la milanesa que comí ayer... estaba una mierda, recomendame otra parrilla buena", el extractor de keywords (`analizar_query_semantica`) parece tomar "milanesa" como keyword además de "parrilla" — pese a que el usuario se está quejando de la milanesa y pidiendo otra cosa. Esto hacía que Hybrid Injection metiera lugares de milanesa (ej. "Lotito's A lo Bestia", especializado en "milanesa a lo bestia", sin ninguna mención de parrilla) para una query que pide parrilla. El fix de categoría (sacar "Argentinian restaurant") terminó tapando el síntoma en este caso puntual, pero la causa de fondo (extracción de keywords en queries de queja/comparación) no se investigó ni se arregló — queda documentado para una sesión futura.

### 🐛 Bug raíz #3: `grupo_alta` rankea por popularidad, no por cuántos términos de la query matchea — pierde el mejor resultado si es poco popular
- **Reportado por el usuario** probando en vivo "parrilla con pelotero": el sistema devolvió como "parecidos" a "El Boliche de Alberto" y "Bambú Canting", pese a que el propio texto de respuesta admitía "no tienen pelotero confirmado" (el resumen de "El Boliche" dice explícitamente "no es para chicos"). Y "827 Punto de Encuentro" — que sí tiene "un pelotero gigante para niños" según su resumen (confirmado leyendo el texto completo) — no apareció en absoluto.
- **Causa raíz (confirmada con trazas + consulta directa a la base real, no hipótesis):**
  1. La clasificación de relevancia usa `any(_mencion_positiva(resumen, t) for t in filtro_terms)` (`main.py:2358`) — con que un lugar matchee **uno solo** de los términos de la query (ej. solo "parrilla", no "pelotero") ya entra a `grupo_alta`. No hay ningún chequeo de que matchee el término específico pedido.
  2. `grupo_alta` se ordena únicamente por `calc_score` (rating + popularidad, `main.py:2365`) antes de cortar a los primeros 12 que van al Juez y a los primeros 2 que van a "Relacionados". "827 Punto de Encuentro" tiene 183 reseñas y 3.4 de rating — muy por debajo de "El Boliche de Alberto" (5256 reseñas) o "Bambú Canting" (5689 reseñas), que solo matchean "parrilla" a medias. El resultado: un match doble y genuino pierde el corte contra matches simples pero populares, y estos últimos nunca llegan a ser evaluados por el Juez (no entran a `candidatos_a_verificar` si tampoco están en el top 12), así que tampoco terminan en `locales_rechazados` — se cuelan en "Relacionados" sin haber sido verificados nunca.
- **Estado: ARREGLADO.** Causa raíz real (encontrada con una traza en vivo de `analizar_query_semantica`, no supuesta): el router recorta la query a **una sola** keyword — el prompt le pedía explícitamente "la palabra clave principal (singular)". Para "parrilla con pelotero" devolvía `keywords: ["parrilla"]` y metía "pelotero" a la basura; nunca se buscaba en Hybrid Injection ni se exigía en la clasificación.
  - **Fix 1 (prompt del router, `main.py:1450` área):** nueva regla 3 — si la query combina categoría/producto CON una característica distinta (parrilla+pelotero, pizzeria+sin tacc), `keywords` debe listar ambos conceptos por separado. Cache key bumpeada a `v91` para invalidar análisis viejos.
  - **Fix 2 (ranking, `procesar_recomendacion_pesado`):** cuando la query tiene más de un concepto en `keywords` (`keywords_core`), `grupo_alta` ahora se ordena por `(match_count, calc_score)` en vez de solo `calc_score` — un lugar que cubre AMBOS conceptos le gana a uno más popular que solo cubre uno. Para queries de un solo concepto el comportamiento es idéntico al anterior (match_count degenera a constante).
  - **Verificado en vivo:** "parrilla con pelotero" ahora trae `Exactos: ['Parrilla Rancho Grande', '827 Punto de encuentro', 'Parrillas Gatica']` — ambos lugares confirmados en el golden dataset aparecen. El Juez ahora rechaza correctamente lugares sin pelotero citando explícitamente "no cuenta con pelotero" en su razonamiento.
  - **Benchmark completo:** 19/22 (antes 20/22) — bajó un caso nuevo, `feature_sintacc`. Investigado con un experimento controlado (mismos `candidatos_crudos` de una búsqueda vectorial real, alimentados a la función vieja committeada vs la nueva): **el resultado es idéntico byte a byte en ambas versiones** — confirma que el fix no toca ese caso. La inestabilidad real es un problema preexistente y distinto: `exactos = locales_verificados[:3]` toma los primeros 3 aprobados **en el orden de la lista de candidatos**, no por relevancia — si el Juez (no determinístico, temperatura>0) aprueba de más a los primeros de la lista (populares pero no específicamente "sin tacc"), le ganan el lugar a los realmente correctos aunque estén más abajo en el orden. Ya existía en el código committeado antes de esta sesión; el golden dataset probablemente lo tenía "por suerte" en corridas anteriores. Queda documentado como bug real, no arreglado — candidato para una futura sesión (ordenar `locales_verificados` por alguna señal de calidad antes de cortar a 3, no por orden de aparición).

### 🐛 Bug de scraping: se coló un resultado de Buenos Aires por matchear la palabra "Neuquén" en su dirección
- **Reportado por el usuario:** "Parrilla La Gran Familia" (aparecía en resultados de "parrilla con pelotero") tenía como dirección real "Neuquén 2252, C1406FOP Cdad. Autónoma de Buenos Aires, Argentina" — a 980km de la ciudad.
- **Causa raíz (repo `que-morfamos-scraper`):** `restaurant-scraper.py` busca en Google Maps con texto libre (ej. `"Parrilla en Zona Oeste Neuquén Capital"`, Selenium, sin acotamiento geográfico/viewport). Google Maps devolvió un resultado suelto de otra ciudad, probablemente porque la dirección real del local contiene literalmente la palabra "Neuquén" (nombre de una calle en CABA). `geo_utils.py::asignar_barrio` sí detectaba correctamente que esas coordenadas caían fuera de todos los barrios oficiales de Neuquén capital (devolvía `barrio=None, zona='Otras Zonas'`), pero nada en `db_utils.py::upsert_lugar` usaba ese resultado para rechazar el alta — se insertaba igual.
- **Alcance verificado** (consulta directa a los 936 lugares por distancia real al centro de Neuquén, no supuesto): además de este caso, apareció "Casa Cóctel - Bar de Eventos" (sin dirección registrada, coordenadas a ~400km, sin ciudad reconocible) — mismo patrón de contaminación. Aparte se encontraron 4 lugares reales y bien reseñados (1300-2000+ reseñas, 4.6-4.7★) en pueblos turísticos genuinos de la provincia de Neuquén pero lejos de la capital (Villa Traful, Piedra del Águila, San Martín de los Andes, Villa La Angostura, 120-300km). El usuario definió explícitamente el alcance: "la idea es que sea regional de neuquen ciudad, ponele que a 20-30 km del centro con toda la furia" — decidió sacar los 4 también, no son parte del producto aunque sean datos reales y de calidad.
- **Fix aplicado:**
  - Nuevo chequeo de distancia (fórmula de Haversine, radio de **30km** desde el centro de Neuquén Capital — cubre Plottier/Cipolletti/Centenario con margen, el más lejano de los tres real es ~15km) agregado directamente en `db_utils.py::upsert_lugar` (`esta_en_zona_cobertura`), **sin importar `geo_utils.py`** a propósito: `db_utils.py` también lo usa `monitor_reviews.py`, que corre en el workflow `actualizar-resenas.yml`, el cual no instala `geopandas` (solo `actualizar-lugares.yml` y `reparar-lugares.yml` lo instalan) — importar `geo_utils` desde `db_utils` habría roto el job semanal de reviews. La función queda autocontenida con solo `math`.
  - Verificado con las coordenadas reales de los casos límite (Buenos Aires, Plottier, Cipolletti, Centenario, Piedra del Águila) — el radio de 30km separa limpiamente "aledaños válidos" (6-15km) de "fuera de alcance" (210km+).
- **Limpieza de datos (con confirmación explícita del usuario antes de cada borrado):** se eliminaron de producción 6 lugares — "Parrilla La Gran Familia", "Casa Cóctel - Bar de Eventos" (contaminación real) y Peumawe/El Galpón Resto Bar/Torino Bar & Bistró/Ay Ay Ay María (fuera del alcance geográfico decidido) — tabla `lugares` (6 filas), `reviews` (2582 filas), `review_history` (147 filas) y sus embeddings en `langchain_pg_embedding` (6 filas). Se sacaron también del `golden_dataset.json` (`plato_milanesas`, `no_falso_bloqueo_queja`, ninguno de los otros 4 estaba en el ground truth) con nota explicando el motivo — no es un error de curación, es data que dejó de existir/estar fuera de alcance.

### 🐛 Hallazgo sistémico: `resumen_reviews` puede alucinar características específicas que ninguna reseña real menciona
- **Reportado por el usuario**, que conoce "Parrillas Gatica" en persona: es un predio municipal de parrillas públicas al aire libre cerca del río, no un local comercial, y le extrañó que estuviera "confirmado" que tiene pelotero.
- **Verificación (esta vez contra las reseñas CRUDAS, tabla `reviews`, no contra `resumen_reviews` generado por IA — mismo error de método que ya había pasado una vez con la curación original del 23/24-ago):** de los 7 lugares que la re-curación del 24-ago había marcado como "pelotero confirmado" leyendo `resumen_reviews`, solo 3 tienen alguna reseña real que use literalmente la palabra "pelotero" (Parrilla Rancho Grande, N&S Food Hall, 827 Punto de Encuentro — probado con citas textuales). Los otros 4 (Parrillas Gatica, Frosts Frineve Creams, Un Altra Volta, Heladería Costa Piré) **no tienen ninguna reseña con esa palabra**: "Parrillas Gatica" no tiene ninguna mención relacionada en absoluto; los otros 3 sí tienen reseñas reales sobre "juegos infantiles", "calesita" o "juegos de plaza" (genuinamente aptos para niños), pero el resumen generado por IA agregó la palabra "pelotero" que ningún reseñador usó.
- **Causa probable:** el LLM que genera `resumen_reviews` (repo `que-morfamos-scraper`, `deepseek_utils.py`) parece asociar "juegos para niños" con "pelotero" por ser una combinación muy común en este tipo de negocio en Argentina, y lo agrega como si fuera un hecho reportado aunque ninguna reseña lo diga. Es una alucinación de enriquecimiento, no un error de traducción/paráfrasis.
- **Por qué importa más que un error de curación puntual:** todo el pipeline de matching de `main.py` (negación, evidencia del Juez, ranking) opera sobre `resumen_reviews` como si fuera la fuente de verdad. Si el resumen mismo alucina un dato específico, ningún fix de matching/ranking lo puede detectar — el texto sobre el que se busca ya está mal. La re-curación del golden dataset había caído en el mismo problema que originó esta sesión (confiar en un texto generado sin verificar contra la fuente primaria), esta vez un nivel más abajo (el resumen de IA en vez de la salida del RAG).
- **Fix del golden dataset:** se sacaron los 4 falsos positivos de `expected_restaurants` en `feature_pelotero`, quedando solo los 3 verificados con cita textual de una reseña real.

- **Causa raíz encontrada y arreglada, `deepseek_utils.py` (repo scraper), función `generar_resumen_reviews`:** el prompt tenía, textual, en la instrucción de "Palabras Clave": *"Incluye explícitamente términos de búsqueda probables (ej: [...] "para niños", "pelotero")"* — le estaba diciendo al LLM que "pelotero" era un ejemplo de palabra a incluir "por probable", no algo a verificar contra las reseñas. Esto explica por qué aparecía repetido en resúmenes de lugares sin ninguna evidencia real.
  - **Fix:** se reescribió la instrucción para que el vocabulario de búsqueda solo se use si está respaldado por las reseñas, se ajustó el párrafo 3 para no completar "por las dudas", y se agregó una regla explícita nueva ("Prohibido inventar") que cubre cualquier característica específica y verificable (pelotero, celíaco, TACC, estacionamiento, wifi, mascotas), no solo pelotero.
  - **Verificado con una regeneración real y acotada** (solo "Parrillas Gatica", 14 reseñas reales, sin tocar la base — prueba aislada): el resumen nuevo dice explícitamente *"No se mencionan en las reseñas servicios como wifi [...] ni la presencia de un pelotero"* — ya no afirma la característica, la niega correctamente cuando no hay evidencia. Éxito.
  - **Bug secundario encontrado por esa misma verificación:** la nueva frase de negación usa "**ni** la presencia de un pelotero" — y `NEGATION_RE` (`main.py`) no incluía "ni" en la lista de palabras de negación (`no|sin|carece|falta|ausencia`), así que `_mencion_positiva` clasificaba esa negación explícita como mención POSITIVA. Se agregó "ni" a `NEGATION_RE`. Verificado con benchmark completo (19/22, mismos 3 casos fallando que antes — no rompió nada) y con casos de control (negación correctamente detectada; una mención positiva genuina con un "ni" no relacionado más adelante en la oración sigue clasificando bien).
  - **Por qué NO se implementó "el Juez pide cita textual" (alternativa que se había planteado):** el Juez (`verificar_candidatos_con_llm`) solo ve `resumen_reviews` — por diseño (arquitectura LAZY v8, para no cargar la tabla `reviews` de 155k filas en cada verificación). Pedirle una "cita textual" al Juez solo lo haría citar el mismo resumen potencialmente alucinado, no la reseña real — no agrega verificación real. El fix tiene que estar en el origen (el prompt del resumidor), no en el Juez.
- **Queda pendiente, fuera de alcance de esta sesión (confirmado con el usuario):** no se regeneraron los `resumen_reviews` de los 930+ lugares existentes en producción con el prompt corregido — implica costo real de API y debería correr como un job aparte (`regenerate_embeddings.py` o similar del repo scraper), a decidir cuándo/cómo en otra sesión. Los resúmenes viejos en producción siguen teniendo el problema hasta que se regeneren.

### 📐 Decisión de producto: "pelotero" es un concepto amplio, no la palabra literal
- **Planteado por el usuario** tras ver la corrección anterior: distinguir "pelotero" (pileta de pelotas) de "juegos infantiles" genéricos (hamaca, tobogán, calesita) es demasiado estricto — a un padre le importa que el chico se entretenga, no el tipo exacto de juego. Además, "Parrillas Gatica" no es ni siquiera un local comercial: es un predio municipal de parrillas públicas (uno lleva su propia carne y cocina ahí), dato que el usuario aportó por conocerlo en persona.
- **Fix (`main.py`, prompt del router):** nueva regla 4 — si la query menciona "pelotero" o similar, el router debe sumar a `synonyms` términos relacionados ("juegos infantiles", "juegos para niños", "área de juegos"). Cache key bumpeada a `v92`.
- **Verificado en vivo:** "lugares con pelotero para chicos" ahora aprueba `['Heladería Costa Piré', 'Parrilla Rancho Grande', 'Frosts Frineve Creams', 'N&S Food Hall', 'Un Altra Volta']` — los 3 con "pelotero" literal MÁS los que tienen juegos infantiles genéricos confirmados en reseñas reales. "Parrillas Gatica" sigue afuera: se comprobó que no tiene NINGUNA mención de juego/niño/hamaca/tobogán en sus 40 reseñas reales, además de no ser un restaurante.
- **`golden_dataset.json` actualizado:** `feature_pelotero` vuelve a sumar Frosts Frineve Creams, Un Altra Volta y Heladería Costa Piré (verificados esta vez por tener evidencia real de juegos, aunque no la palabra exacta), con nota explicando la decisión de producto.
- **Benchmark:** 18/22 — bajó un caso más (`typo_sin_acentos_pizza`, sobre pizzerías, sin ninguna relación con pelotero/juegos). Es la misma fragilidad preexistente de `exactos = locales_verificados[:3]` ya documentada arriba, manifestándose en un caso distinto por el no-determinismo del Juez — no una regresión de este fix.

### 🔬 Auditoría del harness de evaluación: el benchmark no podía medir mejoras
Análisis pedido por el usuario ("evaluación profunda de posibles mejoras para que el benchmark mejore"). El hallazgo central es que **el problema no era el score, era la medición**: varios de los casos no podían fallar, otros no podían pasar, y el ruido era mayor que cualquier mejora medible.

**Diagnóstico (con números, no impresiones):**
1. **El titular estaba inflado ~40%.** 7 de 22 casos no tienen `expected_restaurants` (stats, blocks, followup, sin_resultados) y sacaban Recall=1.00 y MRR=1.00 *vacuamente*, promediados junto a los casos reales. Titular viejo: Recall 0.83. Real, solo sobre casos con ground truth: **0.75**.
2. **`sin_resultados_edge` era un falso PASÓ estructural.** La condición `passed = intent_ok and (recall >= 0.5 or not expected_restaurants)` cortocircuitaba con `expected` vacío: el único caso diseñado para probar "no encontré nada" **era incapaz de fallar**. Y de hecho el RAG devolvía 5 restaurantes reales (McDonald's, Bambú Canting…) para "restaurantes de comida marciana en la luna" — un bug real oculto por la métrica.
3. **Techo matemático en casos con `|expected| > 5`.** `plato_milanesas` y `keyword_vegano` (8 esperados) tenían recall máximo 0.62, `feature_pelotero` (6) máximo 0.83. **Ojo con el diagnóstico inicial:** primero se intentó arreglar ampliando `k` (`effective_k`), pero eso resultó ser un no-op — el techo no viene del corte de la métrica sino de que el backend devuelve **como máximo 5 cards** (`exactos[:3] + relacionados[:2]`). Se revirtió esa función y se atacó el problema real (ver fix 3 abajo).
4. **Ground truth de nombres exactos contra un universo enorme.** `typo_sin_acentos_pizza` devolvió 5 pizzerías reales y sacó 0.20 → FALLÓ. **Hay 52 pizzerías en la base** y el ground truth elige 5 como "las correctas": para queries de categoría/"mejores", pedir nombres exactos de una lista subjetiva es un modelo de evaluación equivocado.
5. **Código muerto:** `zone_match()` estaba definida en `eval_metrics.py` y **nunca se llamaba desde ningún lado** — `expected_zones` estaba poblado en 4 casos pero jamás se evaluaba, así que la detección de zona (feature central) no estaba testeada. `min_results_ok` se calculaba pero no participaba de `passed`.
6. **Precision nunca afectaba el pass/fail.** Un caso podía devolver 4 lugares basura + 1 correcto y pasar.
7. **El ruido supera a la señal.** Con código idéntico, el pass count osciló **20 → 19 → 19 → 18** en la misma sesión. Los LLM ya están en `temperature=0`: el no-determinismo viene de DeepSeek (MoE/batching), y el Juez es la única llamada LLM **no cacheada** del camino crítico. Su salida define `exactos[:3]` → las cards → las métricas.

**Fixes aplicados (`eval_metrics.py`, `run_benchmark.py`, `golden_dataset.json`):**
1. **Scoreboard separado.** Las métricas de retrieval se promedian solo sobre los casos con ground truth (`mide_retrieval`); los de ruteo puro reportan `n/a` en vez de un 1.0 regalado. El resumen aclara "Retrieval medido sobre N/22 casos".
2. **Flag `expected_empty`.** `sin_resultados_edge` ahora FALLA correctamente si devuelve algo. Ya está fallando — expone el bug real que estaba tapado.
3. **Flag `open_ended`** para queries donde el conjunto de respuestas válidas es mucho mayor que los 5 slots disponibles (`plato_milanesas`, `keyword_vegano`): `expected_restaurants` pasa a ser una *muestra de respuestas aceptables*, y el caso se evalúa por **precisión** ("¿los que devolvió son válidos?") en vez de recall contra una muestra arbitraria. Ambos casos pasaron a PASÓ legítimamente: tenían precision 0.60 con recall 0.38 por el techo.
4. **Precision y `min_results` ahora son criterio** de pass/fail (`recall >= 0.5 and precision >= 0.4 and min_results_ok`).
5. **`zone_match` conectada.** Se le pasan las `cards` completas a `evaluate_case` y el chequeo de zona ahora gatea el pass.
6. **Modo estabilidad `BENCHMARK_REPEATS=N`.** Corre la suite N veces y reporta qué casos son inestables y el piso de ruido, en vez de esconderlo: `BENCHMARK_REPEATS=3 python run_benchmark.py`.

**Línea base honesta post-fix:** `19/22 | Recall@5=0.75 | Precision@5=0.69 | MRR=0.90` sobre **16 casos de retrieval** (RECOMMENDATION solo: Recall 0.68 / Precision 0.62). Los 3 que fallan son fallas genuinas, no artefactos:
- `feature_sintacc` (0.00/0.00): falla real y consistente — devuelve cervecerías y heladerías para una query de celíacos.
- `typo_sin_acentos_pizza`: necesita el tipo de aserción por categoría (ver pendientes).
- `sin_resultados_edge`: bug real del sistema, ahora visible.

**Resultado inesperado de la corrida de estabilidad (`BENCHMARK_REPEATS=3`): piso de ruido = 0.** Las 3 corridas dieron `[19, 19, 19]` y **ningún caso cambió de resultado**. Esto corrige el diagnóstico #7 de arriba: la oscilación 20/19/19/18 que se atribuyó al no-determinismo del Juez era mayormente culpa de **las métricas rotas**, no del LLM. `plato_milanesas` y `keyword_vegano` estaban parados justo sobre el umbral `recall >= 0.5` con un techo matemático de 0.62 — cualquier fluctuación mínima del Juez los cruzaba de un lado al otro del corte. Al pasarlos a evaluación por precisión (`open_ended`) quedaron lejos del borde y dejaron de bailar. El Juez probablemente sigue teniendo variación interna, pero ya no se traduce en cambios de pass/fail. **Salvedad:** 3 corridas es una muestra chica; prueba que el ruido bajó muchísimo, no que sea cero para siempre. Vale re-correr `BENCHMARK_REPEATS=3` si en el futuro un resultado parece inestable.

### ✅ Resolución de los 3 pendientes: 19/22 → 22/22
Los 3 casos que el harness corregido dejó al descubierto resultaron ser **5 bugs distintos**, todos encontrados con trazas contra la base real.

**1. `sin_resultados_edge` — el RAG inventaba resultados para queries imposibles.**
- **Primero descarté la vía obvia:** poner un piso de similitud vectorial NO sirve — "comida marciana en la luna" saca score 0.51 y "bares en el rio" 0.55 (en distancia coseno, *menor es más parecido*), o sea la query sin sentido puntúa **mejor** que una legítima. El embedding no distingue absurdo de válido.
- **Causa real (cadena de dos bugs):** el router extrae `keywords: ['restaurante', 'comida marciana']`. La zona detectada ('luna') filtraba a 0 candidatos, pero Hybrid Injection tenía un fallback silencioso: si la zona no matcheaba nada, `df_para_buscar` quedaba como el **corpus completo**, ignorando la zona que el usuario pidió. Y entonces inyectaba por la keyword genérica `"restaurante"`, que matchea **400 de 930 resúmenes**. El Juez después aprobaba 10/10 porque, efectivamente, todos son restaurantes.
- **Fix A — `KEYWORDS_GENERICAS`:** stoplist de palabras que describen el *contenedor* y no el contenido (restaurante, comida, lugar, local, opciones…). No aportan señal de texto. Se aplica tanto en la inyección como en `filtro_terms`. La comparación es por igualdad exacta, así que keywords compuestas ("comida vegana", "comida japonesa") no se ven afectadas.
- **Fix B — zona inexistente ya no degrada en silencio:** si la zona pedida no matchea ningún local del catálogo, se devuelve vacío en vez de buscar en toda la ciudad. Verificado: ahora responde *"No encontré lugares que cumplan con ese requisito específico"*.

**2. `feature_sintacc` (0.00/0.00) — la popularidad estaba inversamente correlacionada con ser correcto.**
- **Los 4 esperados SÍ llegaban al Juez**, pero en las posiciones 11 y 12 de 12. `exactos = locales_verificados[:3]` se quedaba con los tres primeros → Antares (cervecería), Heladería Costa Piré, Diagonal Piré.
- **Por qué:** `grupo_alta` se ordenaba por `calc_score` (popularidad) y en esta query la popularidad juega en contra. Midiendo cuántos términos del filtro menciona cada candidato, la separación es casi perfecta: los correctos matchean 3-4 términos (`sin tacc` + `sin gluten` + `celíaco` + `libre de gluten`) con 60-1023 reseñas; los incorrectos matchean **1 solo** con 2316-5027 reseñas.
- **Fix — clave de orden en cascada** (`relevancia()` reemplaza a `match_count()`): `(conceptos cubiertos, fuerza de evidencia, popularidad)`. El nivel 1 preserva el fix de "parrilla con pelotero" (cubrir 2 conceptos gana); el nivel 2 desempata cuando todos cubren el mismo concepto, que es el caso de "sin tacc"; la popularidad pasa a ser el último criterio, no el primero. Resultado: de 0/4 a **4/4** esperados, sin regresionar pelotero.

**3. `typo_sin_acentos_pizza` — el ground truth era el modelo equivocado, y de paso aparecieron 2 bugs más.**
- **Fix de harness — `expected_category`:** hay **52 pizzerías** en la base y el ground truth elegía 5 por nombre, así que el sistema devolvía 5 pizzerías legítimas y sacaba recall 0.20 → FALLÓ *por acertar*. El caso ahora se evalúa por la `categoria` de lo devuelto (≥60% de las cards deben ser de la categoría pedida), no contra una lista subjetiva de "las mejores".
- **Bug C (regresión que introduje y detecté con el benchmark):** al aplicar el fix B, este caso pasó a devolver **cero** resultados. Motivo: el router detecta `donde: 'neuquen'` — el nombre de la ciudad, no una zona. Como ningún local tiene "neuquen" en su campo `zona`/`barrio` (guardan el barrio), el filtro daba 0 y el fix nuevo devolvía vacío. **Fix:** `ZONAS_NO_FILTRABLES` — pedir "de Neuquén" es pedir toda el área de cobertura, así que se ignora como filtro.
- **Bug D (latente, preexistente, encontrado en el camino):** `aplicar_filtro_zona` comparaba sin normalizar acentos: `'neuquen'` matcheaba **0 lugares** y `'neuquén'` **18**. Cualquier query escrita sin tildes (o sea, la mayoría — el caso se llama literalmente `typo_sin_acentos`) perdía el filtro de zona. **Fix:** se normaliza con `_normalizar_busqueda` de ambos lados. Verificado: `'rio'`/`'río'` → 40 y 40; `'neuquen'`/`'neuquén'` → 18 y 18.

**Resultado: `22/22 | Recall@5=0.87 | Precision@5=0.80 | MRR=0.93`** sobre 16 casos de retrieval (RECOMMENDATION: Recall 0.83 / Precision 0.75). Comparado con la línea base honesta de esta misma sesión (19/22, Recall 0.75, Precision 0.69).

### 🚨 Se agotó el saldo de DeepSeek (producción caída) + defecto en el modo estabilidad
- Al correr `BENCHMARK_REPEATS=3` para confirmar el 22/22, el resultado dio `[22, 6, 3]` y el harness reportó "±19 casos inestables". **No era inestabilidad:** la corrida 1 fue limpia (0 errores, 22/22 legítimo) y a partir de la corrida 2 la API de DeepSeek empezó a devolver `HTTP 402 Payment Required — Insufficient Balance`.
- **Producción también estaba caída** por lo mismo: `POST /chat` contra Fly.io respondía *"Tuve un problema técnico buscando eso."* con cero cards. El backend usa DeepSeek para el router, el Juez y la generación; la key de OpenAI sí tiene saldo (los embeddings funcionaban durante toda la corrida), así que un `AI_PROVIDER=openai` en los secrets de Fly es la salida rápida si hace falta.
- **Defecto propio del harness (arreglado):** `main.py` atrapa los errores de LLM y responde HTTP 200 con un texto de fallback, así que una API caída se veía como "todos los casos fallaron a la vez" y el modo estabilidad lo reportaba como ruido. Ahora `run_single_case` marca `api_error` cuando la respuesta empieza con el texto de fallback, y el reporte avisa en rojo que la medición no es confiable en vez de inventar un piso de ruido.
- **Pendiente de re-verificar:** el 22/22 está confirmado por una sola corrida limpia. Volver a correr `BENCHMARK_REPEATS=3` cuando haya saldo, para confirmar estabilidad.

### 📝 Pendientes para retomar (no son bugs, son tareas abiertas)
- **Minar `query_logs` para ampliar el golden dataset:** la tabla y el logging ya están activos en producción desde la sesión del 24-ago (ver arriba), pero todavía no se usó para nada — hay que esperar a que se acumulen suficientes queries reales y después revisarlas para sumar casos nuevos (o más realistas) al golden dataset. No hay una fecha ni un umbral definido; es simplemente "volver a esto en unas semanas".
- **Tab de LangSmith en el dashboard (`que-morfamos-dashboard`):** se evaluó agregar una solapa propia para visualizar las corridas del golden dataset (el dashboard ya tiene el patrón — Next.js + Radix Tabs + d3 — y sería sencillo pegarle a la REST API de LangSmith desde ahí). Se decidió posponerlo: por ahora alcanza con la UI propia de LangSmith (`smith.langchain.com`, proyecto `que-morfamos`, dataset `que-morfamos-golden` → pestaña Experiments), que ya da comparación entre corridas sin construir nada. Retomar esto si en algún momento se quiere todo centralizado en el dashboard del portfolio en vez de saltar a otra pestaña.
- **Bug preexistente encontrado al investigar `feature_sintacc`:** `exactos = locales_verificados[:3]` (`main.py`, cerca de `procesar_consulta_gen`) toma los primeros 3 aprobados por el Juez en orden de lista, no por relevancia — un lugar popular pero tangencial puede ganarle el lugar a uno más específico si el Juez es permisivo. Confirmado con experimento controlado que **no** es causado por los fixes de esta sesión (existía antes, committeado). Candidato para una futura sesión: ordenar `locales_verificados` por alguna señal de calidad/especificidad antes de cortar a 3.

---
*Bitácora actualizada por Antigravity Agent (v7.1).*
