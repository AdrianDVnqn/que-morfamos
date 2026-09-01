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

### 💸 Cambio de proveedor LLM y comparación de alternativas (26-ago-2026)
Tras quedarse sin saldo DeepSeek, se evaluaron alternativas con pruebas reales de API (no de memoria: varios de estos modelos son posteriores al cutoff del asistente). Estimación de costo usada: **~10k tokens de input + ~3k de output por consulta** (el pipeline hace ~8 llamadas LLM: router, Juez, generación de texto y una por card).

| Candidato | $/1M in–out | $/consulta | Veredicto |
|---|---|---|---|
| **gpt-4o-mini** | 0.15 / 0.60 | **$0.0033** | ✅ Elegido |
| gpt-5-nano | 0.05 / 0.40 | $0.0017 nominal → **~$0.0125 real** | ❌ Ver abajo |
| gpt-5.6-luna | 0.20 / 1.20 | $0.0056 | ❌ 1.7x más caro; parte keywords mal |
| gpt-5.6-terra | 2.00 / 12.00 | $0.056 | ❌ 17x más caro |
| gemini-3.5-flash-lite | 0.30 / 2.50 | $0.0105 | ❌ 3x más caro (free tier inusable) |
| deepseek-v4-flash | 0.22 / 0.66 | $0.0042 | ❌ Ya no es la opción barata |

**Hallazgos que sólo aparecieron probando la API de verdad:**
- **gpt-5-nano es un modelo de razonamiento, y eso invalida su precio por token.** Para una tarea trivial de routing gastó **1408 tokens de razonamiento** (facturados como output) en modo default, 448 en `low` y 0 en `minimal`. Sin `reasoning_effort=minimal` sale ~4x más caro que gpt-4o-mini, no la mitad. La estimación inicial basada sólo en precio por token estaba mal.
- **gpt-5-nano confunde "pelotero" con béisbol**: extrajo `["beisbolista","lanzador","pitcher"]` — justo la query que costó media sesión arreglar.
- **Toda la familia gpt-5 rechaza `temperature=0`** (sólo acepta el default 1) y exige `max_completion_tokens` en vez de `max_tokens`. LangChain traduce el parámetro solo, pero el no-determinismo forzado choca con un pipeline que depende de JSON estricto — y con el benchmark estable que se acababa de construir.
- **gpt-5.6-luna parte mal las keywords**: devolvió `["parrilla con pelotero"]` como un único concepto en vez de dos. Eso degenera `keywords_core` a 1 y anula el orden en cascada que hace aparecer a "827 Punto de Encuentro". (Salvedad: se probó con un prompt simplificado; el prompt real de `main.py` tiene la regla explícita y podría corregirlo.)
- **El "80% lower cost" de gpt-5.6-luna es relativo a Sol/Terra**, no barato en términos absolutos: sigue siendo 1.7x gpt-4o-mini.
- **Gemini: excelente calidad, free tier inusable.** El router devolvió exactamente `["parrilla","pelotero"]` con sinónimos correctos y cero alucinación, y el texto generado tenía muy buen tono rioplatense. Pero el free tier throttlea fuerte: llamadas triviales ("di OK", 20 tokens) dieron timeout a los 25s dos veces seguidas y luego 15.2s de latencia; una consulta completa del pipeline tardó **más de 180 segundos**. Inviable para un chat. Y `gemini-2.5-flash-lite` (el barato, $0.10/$0.40) **ya no está disponible para cuentas nuevas** — Google redirige a `3.5-flash-lite`, 3x más caro que gpt-4o-mini.
- **DeepSeek dejó de ser la opción barata** y `deepseek-chat` ya no figura en su catálogo (ahora `deepseek-v4-flash`/`v4-pro`), así que el `model="deepseek-chat"` del código apunta a un ID viejo.

**🐛 Bug grave que sólo apareció al correr el benchmark en el proveedor nuevo:** con gpt-4o-mini el resultado cayó a **19/22** (Intent Accuracy 0.95). Causa raíz única para los tres fallos: **el router de gpt-4o-mini devuelve `synonyms: []` siempre**, mientras DeepSeek devolvía listas ricas (`['sin gluten','apto celíaco','libre de gluten']`). Como el desempate por fuerza de evidencia en `relevancia()` se calcula sobre keywords ∪ sinónimos, con la lista vacía todos los candidatos empatan y el orden vuelve a caer en popularidad — **reapareciendo exactamente el bug de `feature_sintacc` que se había arreglado horas antes**, con los mismos lugares equivocados (Antares, Heladería Costa Piré…). El fix del ranking estaba acoplado a una peculiaridad de DeepSeek sin que nadie lo notara.
- **Fix: `SINONIMOS_CURADOS` + `expandir_sinonimos()`** — mapa estático para los conceptos que más pesan (sin tacc, vegano, vegetariano, pelotero, celíaco), mismo patrón que el `KEYWORD_TO_CATEGORIES` que ya existía. El ranking deja de depender del capricho del LLM. Matchea por prefijo para cubrir plurales/género y **suma el concepto canónico además de sus sinónimos**, porque la forma que escribe el usuario ("veganas") no matchea el texto del corpus ("vegano").
- **Fix del router:** `"que onda el growler"` se ruteaba a `rag` en vez de `resumen`. El prompt sólo tenía ejemplos capitalizados y con signo (`"Que onda Atila?"`), así que gpt-4o-mini no leía `"el growler"` en minúscula como nombre propio. Se agregó ese caso explícito a la REGLA DE ORO (cache key a `v93`).
- **Resultado tras los fixes: `22/22 | Recall@5=0.86 | Precision@5=0.80 | MRR=0.97 | Intent Accuracy=1.00`** sobre gpt-4o-mini — paridad con DeepSeek (0.87/0.80/0.93) y mejor MRR.
- **Lección:** el golden dataset se midió siempre sobre un proveedor; cambiar de LLM puede romper fixes que parecían generales. Vale re-correr el benchmark ante cualquier cambio de proveedor o de modelo, no sólo ante cambios de código.

**Cambios de código:**
- Nuevo modo `AI_PROVIDER=openai-mini` (gpt-4o-mini para ambos LLMs): con saldo acotado rinde ~1200 consultas donde `openai` (mini + gpt-4o) rinde ~70.
- Nuevo modo `AI_PROVIDER=gemini` vía el endpoint compatible con OpenAI de Google (`generativelanguage.googleapis.com/v1beta/openai/`), mismo patrón que DeepSeek — sin SDK nuevo.
- Los IDs de modelo son configurables por env (`OPENAI_MINI_MODEL`, `OPENAI_SMART_MODEL`, `GEMINI_MODEL`, `DEEPSEEK_MODEL`) para poder comparar proveedores contra el golden dataset sin tocar código.

### 🧪 Regeneración de resúmenes: 4 iteraciones, shadow write, y la decisión de NO promover
Objetivo: eliminar de la base los resúmenes con características inventadas (y, en el camino, los truncados). Se hizo con **shadow write** (blue-green para datos derivados): columnas y colección de vectores paralelas, sin tocar nunca lo que sirve producción. **Resultado: v4 tiene datos objetivamente mejores pero peor retrieval, así que NO se promovió.**

**Infraestructura construida (`que-morfamos-scraper`):**
- `deepseek_utils.py` → **`llm_utils.py`** (renombrado con `git mv`; ya no era específico de DeepSeek). Multi-proveedor vía `SUMMARY_PROVIDER` (deepseek|openai|gemini) y `SUMMARY_MODEL`. **Esto además desbloqueó el job semanal del scraper, que estaba muerto**: seguía cableado a DeepSeek, sin saldo desde el 26-ago. Verificado que ningún workflow de GitHub Actions referenciaba el módulo (invocan scripts, no el módulo) antes de renombrar.
- `regenerar_resumenes.py` (nuevo): regeneración idempotente y resumible con shadow write a `resumen_reviews_v2` + `resumen_prompt_version` + `resumen_generado_at`, embeddings a la colección `reviews_embeddings_v2`, concurrencia 6, `--dry-run` con estimación de costo.
- `regenerar_muestra.py` (nuevo): compara viejo vs nuevo sobre una muestra e incluye un **chequeo automático de groundedness** (¿alguna reseña cruda respalda cada característica que el resumen afirma?).
- `main.py` (backend): `RESUMEN_COLUMN` para poder evaluar la columna shadow con el benchmark sin promover nada.

**Las 4 iteraciones — cada fix destapó el siguiente:**
1. **v2 — prompt sin alucinaciones.** El prompt viejo pedía literalmente incluir "términos de búsqueda probables (ej: … 'pelotero')". Corregido → afirmaciones sin respaldo **33 → 0**, truncados **127 → 0**, sin 3er párrafo **51 → 0**. Pero el benchmark cayó a **19/22**.
2. **Diagnóstico:** el problema NO era el prompt sino el **muestreo**. `muestreo_estrategico` toma 50 reseñas (recientes/largas/extremas/random) y las 2 de 379 que mencionan "pelotero" en Parrilla Rancho Grande quedaban afuera **siempre**: el LLM nunca veía la evidencia. **La alucinación venía enmascarando un bug de muestreo preexistente.**
3. **v3 — muestreo dirigido por característica**, con **cuota por grupo** (`FEATURE_GROUPS`, 3 reservados por característica). La cuota por grupo la planteó el usuario: sin ella, un lugar con 30 reseñas veganas llenaba los lugares reservados y las 2 de pelotero se perdían igual. Features detectables 11/18 → 14/18.
4. **v4 — prompt simétrico + `temperature=0`.** v3 había quedado con sesgo negativo: leía *"pedimos un desayuno sin TACC y tuvimos variedad"* y escribía *"escasez de opciones sin gluten"*. Se agregaron reglas explícitas de "ni inventar ni negar". Y se bajó `temperature` de 0.3 a 0: con 0.3 dos corridas sobre las mismas reseñas daban resúmenes distintos, lo que hacía imposible atribuir una mejora al prompt en vez de al azar. Features detectables **17/18** (el 18º lo omite **correctamente**: no tiene evidencia).

**Bugs colaterales encontrados y arreglados en `_mencion_positiva` (`main.py`):**
- *"opciones veganas y **sin** gluten"* daba False para "gluten": el "sin" de una feature POSITIVA se leía como negación. Fix: si el término va precedido inmediatamente por "sin", ese "sin" es parte de la feature.
- Una negación de **otra oración** contaminaba la ventana de 70 caracteres: en *"…sin gluten. El ambiente es acogedor, con áreas de juegos"*, el "sin" negaba a "juegos". Fix: la ventana se corta en el límite de oración (`.`/`;`/salto de línea).

**Re-curación del ground truth (26-ago), esta vez contra RESEÑAS CRUDAS de toda la base:** búsqueda SQL independiente del sistema sobre `reviews`, criterio ≥2 reseñas distintas mencionando el concepto. `keyword_vegano` pasó de 8 a 18 lugares y `feature_pelotero` de 6 a 12 — la lista vieja salía de leer `resumen_reviews` v1 (que alucinaba) y era además **muy incompleta**: dejaba afuera lugares con evidencia fuerte (Cervecería OSS con 16 menciones veganas, Rio Juegos + Café con 13 de juegos). Se excluyeron *Cuchí* (cerrado) y *Oh My Veggie - Av. Argentina 368* (0 reseñas lo mencionan; estaba sólo por el resumen alucinado). Ambos casos quedaron `open_ended`.

**La decisión, con la medición hecha en igualdad de condiciones** (mismo ground truth re-curado, mismo proveedor, ambas versiones):

| | v1 (producción) | v4 |
|---|---|---|
| Casos | **22/22** | 18/22 |
| Recall@5 | **0.83** | 0.73 |
| Precision@5 | **0.84** | 0.68 |
| MRR | **0.97** | 0.82 |

- **Se descartaron dos hipótesis propias por evidencia en contra:** (a) que el problema fuera el prompt (era el muestreo) y (b) que el benchmark castigara injustamente a v4 por encontrar lugares válidos no listados — al ampliar el ground truth con evidencia real, v1 **igual** gana en todas las métricas, y su precisión incluso *sube* (0.80 → 0.84), o sea ya venía devolviendo lugares válidos que el ground truth viejo no le acreditaba.
- **Mecanismo de la diferencia:** v1 devuelve los lugares con evidencia **más fuerte** (Ohana con 23 menciones veganas, BIO ZEN vegetariano dedicado) con precisión 1.00 en los tres casos de feature; v4 devuelve lugares marginales (una pizzería con una opción vegana, un bar con 3 menciones). Para el usuario que busca "opciones veganas" eso es peor, aunque v4 sea más honesto.
- **Hipótesis para una v5:** los resúmenes v1 son más largos (~2000 vs ~1500 chars) y enfatizan más las características, produciendo embeddings más ricos. Lo que le falta a v4 no es menos invención sino **más riqueza** — subir el detalle exigido en el párrafo 3, no bajarlo.

**Estado:** v4 queda en las columnas y colección shadow, sin promover. Producción sigue en `resumen_reviews` / `reviews_embeddings`, verificada en 22/22. Costo total de las 4 regeneraciones: ~$1.32.

### 🐛 Sinónimos de una sola dirección: "parrilla con pelotero" andaba, "parrillas con juegos" no
- **Reportado por el usuario** probando en producción: `"recomendame parrillas con juegos para niños"` devolvía 5 lugares de los cuales **uno solo era una parrilla** (los otros: McDonald's, N&S Food Hall, una heladería y un buffet). La respuesta *parecía* correcta porque todos tenían juegos para chicos — pero ignoraba por completo la mitad "parrilla" del pedido.
- **Causa raíz:** `expandir_sinonimos` comparaba la keyword sólo contra la **clave** de cada concepto en `SINONIMOS_CURADOS`, nunca contra sus sinónimos. El mapeo quedaba en una sola dirección: `"pelotero"` expandía a `["juegos infantiles", …]`, pero una query que dijera `"juegos"` **no** expandía a `"pelotero"`. Además `relevancia()` contaba conceptos cubiertos usando la keyword literal. Resultado, para la query `['parrilla','juegos']`:

  | lugar | parrilla | juegos | conceptos |
  |---|---|---|---|
  | 827 Punto de encuentro | True | **False** (dice "pelotero") | 1 |
  | Parrillas Gatica | True | **False** | 1 |
  | McDonald's | False | True | 1 |

  Todos empatados en 1 concepto → desempata la popularidad → gana McDonald's con 10.033 reseñas. Por eso la misma búsqueda escrita como "parrilla con **pelotero**" sí funcionaba.
- **Fix:** nuevo helper `variantes_de_concepto(keyword)` que devuelve la keyword más toda la familia de su concepto, y `expandir_sinonimos` ahora empareja la keyword contra la clave **y** contra los sinónimos (`_emparenta`, prefijo en cualquier dirección). `relevancia()` cuenta un concepto como cubierto si el resumen menciona **cualquier** variante. Las parrillas con pelotero pasaron de 1 a 2 conceptos.
- **Verificado:** `"recomendame parrillas con juegos para niños"` ahora incluye "827 Punto de encuentro" (parrilla real con pelotero), que antes no aparecía. Benchmark completo `22/22 | Recall 0.83 | Precision 0.83 | MRR 0.97` — sin regresión respecto de la línea base (0.84 de precisión, dentro del ruido).
- **Nota:** la respuesta sigue incluyendo lugares que no son parrillas en los slots de "relacionados". Eso es el pendiente #3 (`exactos = locales_verificados[:3]` corta por orden de lista, no por relevancia), no este bug.

### 🗺️ CARTO dejó de servir su tier anónimo (frontend)
El mapa de `que-morfamos-frontend` usa `https://{s}.basemaps.cartocdn.com/dark_all/…` sin API key (`src/App.js`, `MAP_STYLE`). CARTO ahora exige key y superpone una marca de agua. Las tiles **siguen cargando** (HTTP 200), así que no es una caída: es estético. Opciones evaluadas: (a) API key gratuita de CARTO — mantiene el look exacto, pero la key queda pública en el bundle y hay que acotarla por dominio; (b) OSM + filtro CSS oscuro — sin registro ni keys, look parecido pero las etiquetas quedan con colores invertidos; (c) Stadia Maps — estilo oscuro nativo, también con key. **Pendiente de decisión del usuario.**

### 🐛 Selección y orden de resultados: la relevancia se tiraba a la basura en la última línea
- **Continuación del bug anterior**, pedido por el usuario: aun con los sinónimos bidireccionales arreglados, la respuesta seguía metiendo no-parrillas en los slots de "alternativas". Al abrir el código aparecieron **dos** puntos de pérdida, no uno:
  1. `exactos = locales_verificados[:3]` cortaba por el orden en que el **Juez** devolvió los nombres, no por relevancia.
  2. **El más grave, que no estaba documentado:** justo antes de mostrar había un `exactos.sort(key=get_score)` / `relacionados.sort(key=get_score)` con `get_score = rating + log(reseñas)`. Es decir, **re-ordenaba todo por popularidad en la última línea**, tirando a la basura el ranking en cascada calculado aguas arriba. Un McDonald's de 10.033 reseñas terminaba arriba de una parrilla con pelotero de 183.
- **Cuatro combinaciones probadas y medidas** (selección × orden), porque cada intento rompía algo distinto y sólo se veía al medir:

  | Selección | Orden mostrado | Recall | Precision | MRR |
  |---|---|---|---|---|
  | Juez | popularidad | 0.83 | 0.83 | **0.97** ← línea base |
  | relevancia | relevancia | 0.83 | 0.81 | 0.91 |
  | popularidad | popularidad | 0.78 | 0.80 | 0.86 |
  | relevancia | condicional | 0.81 | 0.81 | 0.91 |
  | **condicional** | **condicional** | **0.83** | **0.83** | **0.97** ✅ |

- **Por qué la relevancia empeora en queries de UN concepto:** con "parrilla" todos los candidatos empatan en `conceptos=1`, así que desempata `evidencia` (cuántos sinónimos menciona el resumen) — que premia resúmenes verbosos, no lugares buenos. Un local oscuro que escribe "parrilla, asado, parrillada" le ganaba a Parrilla Rancho Grande (4.3⭐, 1532 reseñas). Medido: `no_falso_bloqueo_queja` caía de MRR 1.00 a 0.33.
- **Fix final, en un principio simple:** *la cobertura de conceptos sólo se usa cuando hay más de un concepto que cubrir*. Tanto la selección como el orden son condicionales a `multi_concepto`; las queries de un solo concepto pasan por exactamente el mismo código que antes, así que **no pueden regresionar por construcción**.
- **Resultado:** `22/22 | Recall 0.83 | Precision 0.83 | MRR 0.97` — idéntico a la línea base, con el bug arreglado. Verificado end-to-end: "recomendame parrillas con juegos para niños" devuelve Rancho Grande y 827 Punto de Encuentro arriba; "recomendame otra parrilla buena" recuperó Rancho Grande en primer puesto.
- **Nota metodológica:** en el camino afirmé que ordenar por relevancia era mejor porque ponía BIO ZEN (vegetariano dedicado, 117 reseñas) antes que Lucciano's (heladería, 4686). Eso era **preferencia propia, no medición** — el golden dataset tiene a los dos como válidos, así que el benchmark no los distingue. Se revirtió esa parte y se conservó sólo lo medido.

### ⚡ Análisis de rendimiento y primer fix: la búsqueda vectorial hacía Seq Scan (27-ago-2026)
Medición de los dos flujos que el usuario reportó como lentos, contra producción y en local con los `[TIMING]` internos.

**Flujo 1 — consulta (`/chat/stream`), lo que ve el usuario en producción:**

| Hito | Tiempo |
|---|---|
| Primer texto visible | **11.6 – 14.9s** |
| Cards | 17.7 – 22.1s |

Desglose local (13.7s total): embedding OpenAI 1.3s + **consulta pgvector 2.6s** + router LLM 2.0s (paralelo) + Juez ~1.5s + generación hasta el primer token.

**Flujo 2 — click en card (`/restaurant`), ~9s en producción:**

| Etapa | Tiempo |
|---|---|
| Metadata | 0.02s |
| Reviews (DB) | 1.0s |
| **Análisis LLM** | **2.2 – 4.4s** |

#### 🔧 Arreglado: la columna de embeddings no tenía dimensión fija
`EXPLAIN ANALYZE` mostró un **`Seq Scan`** sobre las 909 filas de la colección: 568ms de CPU en la base, en **cada** consulta del chat. Causa: LangChain crea `langchain_pg_embedding.embedding` como `vector` **sin dimensión**, y con ese tipo Postgres no puede optimizar el cálculo de distancia. Además pgvector rechaza crear un índice HNSW sobre una columna así (`column does not have dimensions`).

Fix (script idempotente `crear_indice_vectorial.py`): `ALTER COLUMN embedding TYPE vector(1536)` + `CREATE INDEX CONCURRENTLY ... USING hnsw`. Verificado que las 1700 filas tenían la misma dimensión y sin nulos antes de tocar nada.

- **DB: 568ms → 23.6ms (24x).** A nivel app la búsqueda bajó de 2.64s a ~1.1s (lo que queda es ida y vuelta de red, ver abajo).
- **Nota honesta:** la mejora viene del `ALTER`, no del índice. El planificador sigue eligiendo `Seq Scan` porque la consulta filtra por `collection_id` y pgvector no combina bien HNSW con filtros. El índice queda creado por si la tabla crece; si hiciera falta, el camino es un índice **parcial** por colección.

#### 🔴 Pendiente #1: el caché de Redis está muerto en producción
Tres llamadas **idénticas** a `/restaurant` dieron 8.75s / 8.67s / 8.84s. Si Redis funcionara, la segunda sería instantánea (el endpoint retorna temprano con `cached_full`). `UPSTASH_REDIS_REST_URL` y `UPSTASH_REDIS_REST_TOKEN` están **vacías** en el `.env` local; todo indica que nunca se configuró la instancia.

No afecta sólo a las cards: el código **ya cachea** la búsqueda vectorial (`vsearch`) y el análisis del router (`analysis_v93`). Con Redis caído, **cada consulta rehace todo desde cero**, aunque alguien busque exactamente lo mismo. Es el fix de mayor impacto pendiente y no requiere tocar código: crear una instancia gratuita de Upstash y cargar las dos variables en Fly.io.

#### 🟠 Pendiente #2: la app y la base están en costas opuestas
`fly.toml` fija `primary_region = "ewr"` (Newark, este) y la base es `aws-0-us-west-2` (Oregon, oeste). Cada round trip a la base cruza el continente, y el request hace varios. Cambiar la región de Fly a una cercana a la base (`sjc`/`sea`) es una línea en `fly.toml`, gratis. Ganancia estimada: modesta (~0.5s), porque lo que domina son las llamadas al LLM.

#### 🟠 Pendiente #3: cold start de ~46s
`min_machines_running = 0` + `auto_stop_machines = "stop"`: la primera visita después de un rato paga el arranque completo (medido: 46s). Ponerlo en 1 lo elimina, pero implica tener la máquina siempre encendida — tiene costo.

#### 🟠 Pendiente #4: el click en card bloquea esperando al LLM
`/restaurant` espera 2-4s a que el LLM genere un resumen específico del tópico antes de responder. La metadata y las reseñas ya están listas en ~1s. Devolver eso primero y cargar el análisis aparte haría que la card se sienta inmediata.

## 📅 Sesión: 28 de Agosto de 2026 (Contaminación por query, orden de reseñas, frase destacada y fichas muertas de Google)

### 🎯 Objetivo
Arrancó como un reporte puntual del usuario —"busco opciones veganas, aparece Ohana y el detalle dice que no menciona opciones veganas"— y terminó destapando cuatro bugs independientes en el mismo camino, más el detector de locales cerrados que estaba pendiente desde el 26-ago.

### 🐛 Bug #1: el detalle afirmaba una ausencia sin haber visto una sola reseña del tema
- **Síntoma:** para `topic="opciones veganas"`, el detalle de Ohana Tienda y Cafe decía *"no se menciona específicamente opciones veganas en las experiencias compartidas"* y listaba eso como **"A mejorar"**.
- **Lo que NO era:** ni un problema de recuperación ni una alucinación. Ohana es un resultado legítimo — su propio `resumen_reviews` dice *"incluyendo opciones veganas, vegetarianas y sin TACC"* — y los otros 4 resultados de esa búsqueda también lo eran (se verificó uno por uno, incluido Lucciano's, que tiene "variedad de helados veganos" en su resumen). El modelo describió con exactitud lo que le mostraron.
- **Causa real, medida:** el endpoint llamaba `obtener_reviews_por_local()` **sin `terminos`**, así que traía las 15 reseñas más recientes y recién después las rankeaba por tema. Ohana tiene 548 reseñas y 43 mencionan vegano/vegetariano/sin TACC, pero:

      hoy (15 más recientes)          -> 0 de 15 mencionaban el tema
      pasando términos a la consulta  -> 15 de 15

  `_fetch_reviews_sync` ya soportaba ordenar por relevancia desde la sesión del pelotero; este endpoint simplemente no lo usaba.
- **Fix (tres defectos que se acumulaban):**
    1. Los términos del tema van a la consulta, expandidos con `variantes_de_concepto()` para que "veganas" alcance también "vegano" y "vegetariano".
    2. La muestra que ve el LLM pasa de 150 a 400 caracteres por reseña. En Ohana había menciones al tema recién en el caracter 185 y 249: el recorte las cortaba.
    3. El prompt decía *"El usuario busca X. Resaltá qué dicen las reseñas sobre eso"*. Si no decían nada, el modelo reportaba la ausencia — y `negativos` es el único casillero donde entra una mala noticia sobre la búsqueda. Ahora rige la misma regla que el prompt de resúmenes del scraper: **ni inventar ni negar**, y `negativos` es sólo para quejas concretas.
- **Resultado verificado sobre el mismo caso:** el "A mejorar" pasó de *"No se mencionan opciones veganas"* a *"Las opciones veganas eran desabridas"* y *"olor extraño a cloacas"* — quejas reales, rastreables palabra por palabra a reseñas concretas.

### 🐛 Bug #2: el ordenamiento por fecha de reseñas era un no-op
- **Causa:** `fecha_a_orden()` se escribió para las fechas relativas en español que scrapeaba Google ("hace 2 meses"), pero la base guarda ISO desde hace rato. A una fecha ISO le caían todos los `if` y devolvía el `5000` del final, **igual para todas**:

      2026-08-22 -> 5000
      2026-08-09 -> 5000
      2026-07-27 -> 5000

- **Alcance:** afectaba a las cuatro llamadas de `rankear_reviews_por_topico`, incluida la frase destacada de las tarjetas.
- **Fix:** las ISO se convierten a horas transcurridas, la misma escala que ya usaba la rama de fechas relativas.
- **Pero con eso solo no alcanzaba** para que la fecha decidiera algo:
    - La relevancia por tema pasa a ser **binaria**. Antes sumaba 100 por keyword matcheada y después +50 si el rating era alto o −20 si era bajo, así que entre dos reseñas del tema ganaba la de mejor puntaje y la fecha casi nunca desempataba. El bonus por rating además empujaba las quejas hacia abajo, que en una app de reseñas es justo lo que no se quiere. Ahora la relevancia elige **qué** reseñas y la fecha decide **en qué orden**.
    - `get_keywords_from_topic()` ahora descarta las palabras-contenedor de `KEYWORDS_GENERICAS`, que ya existía pero esta función no usaba. "opciones veganas" dejaba `["opcion","vegana"]`, y "opcion" matchea cualquier reseña que diga "opciones de almuerzo" u "opciones sin gluten".

### 🐛 Bug #3: la frase destacada de las tarjetas — cuatro defectos encadenados
1. **Selección de términos NO DETERMINISTA.** `search_terms` es un `set` y `_fetch_reviews_sync` se queda con los primeros 6: cuáles 6 sobrevivían cambiaba entre corridas. Tres ejecuciones del mismo corte dieron tres subconjuntos distintos, y a veces entraba "opciones" dejando afuera "vegano".
2. **No se verificaba que la cita hablara del tema:** se agarraba la primera fila con más de 20 caracteres. En una búsqueda vegana, Ohana citaba *"La carta estaba desactualizada, no tenían varios ingredientes"*. Ahora se exige mención, se descartan las negadas con `_mencion_positiva` (*"lo único malo es que NO tienen parrillas"* contenía el término y era lo contrario de una evidencia) y entre candidatas gana la que menciona más términos distintos.
3. **El recorte a 200 caracteres tapaba la evidencia.** Una reseña podía calificar por decir "las medialunas veganas también" en el caracter 139 y mostrarse cortada justo antes. Ahora la ventana se abre alrededor de la mención.
4. **Sin reseña del tema se fabricaba una cita:** se ponía `resumen_reviews` —texto generado por un modelo— entre comillas y firmado *"— Google Reviews"*, y el frontend lo renderiza en itálica con la atribución. Un texto que no escribió nadie aparecía como el testimonio de un cliente. Ahora simplemente no se muestra cita.

### ⚖️ Decisión de producto: la cita de la tarjeta prefiere reseñas positivas
- **Planteo del usuario:** "si uno busca mejores pizzas y lee reseñas negativas, te hace dudar de todo el sistema. TODO lugar va a tener reseñas negativas."
- **Razonamiento:** una cita elegida sin mirar la valencia no da una visión balanceada — da una muestra de tamaño 1 con signo aleatorio. Y como todo lugar tiene reseñas malas, tampoco distingue un lugar bueno de uno malo. Un local de 4.1★ con 3476 reseñas ilustrado con su peor comentario sólo hace dudar del sistema entero.
- **Implementación:** entre las candidatas que mencionan el tema positivamente ganan las de 4-5★ y se descartan las de 1-2★. Si no queda ninguna, no se muestra cita: no se fabrica un positivo que no existe.
- **Por qué no contradice haber sacado el bonus por rating del ordenamiento (bug #2):** son propósitos distintos. Ahí el objetivo es **no esconder quejas en una lista**; acá es **elegir un ejemplar representativo** de una recomendación. Lo negativo sigue visible en "A mejorar" y en la lista completa de reseñas, a un clic.
- **Verificado:** Franz y Peppone pasó de *"Salón abandonado, muebles viejos, paredes sucias"* a *"Riquísimas las pizzas! Hace 20 años soy cliente"*.

### 🐛 Bug #4: `variantes_de_concepto()` devolvía duplicados
`SINONIMOS_CURADOS` es bidireccional a propósito, así que ambas entradas matchean y la lista salía repetida: para "vegano" devolvía 11 términos de los que sólo 7 eran distintos, con "vegano" **tres veces**. Rompía dos cosas: `evidencia` suma 1 por término, así que una sola palabra valía triple; y `_fetch_reviews_sync` corta en 6, y los repetidos gastaban esos lugares. El orden no cambia en los casos conocidos — era una distorsión del puntaje, no del resultado.

### 🔍 Cómo se explica el orden de resultados (pregunta del usuario, vale documentarla)
Ante *"¿por qué ÁRBOL aparece 5to siendo 4.6★ con 1153 reseñas, y Oh My Veggie 2do con 4.2★ y 572?"*, el desglose de la clave en cascada `(conceptos, evidencia, popularidad)`:

| lugar | conceptos | evidencia | popularidad | rating/reseñas |
|---|---|---|---|---|
| BIO ZEN | 1 | **4** | 6.97 | 4.9★/117 |
| Oh My Veggie | 1 | **4** | 6.96 | 4.2★/572 |
| Ohana | 1 | 3 | 7.51 | 4.6★/816 |
| Lucciano's | 1 | 2 | 8.47 | 4.8★/4686 |
| ÁRBOL | 1 | **2** | 7.66 | 4.6★/1153 |

Los cinco cubren el concepto, así que desempata la **evidencia**: Oh My Veggie menciona 4 de 7 términos del concepto; ÁRBOL sólo 2, porque es un brewpub que **además** tiene opciones veganas. La popularidad entra tercera. Si no fuera así, toda búsqueda devolvería los 5 lugares más famosos de Neuquén sin importar qué se preguntó.

### 🗑️ "Cuchí" eliminado de la base (pendiente #2 del 26-ago, resuelto para este caso)
- **Alcance medido antes de borrar:** 1 fila en `lugares`, 315 en `reviews`, 26 en `review_history`, 26 en `scraping_logs` (las tres últimas por `ON DELETE CASCADE`, que ya estaba configurado) y **2** en `langchain_pg_embedding`.
- **Casi un accidente serio:** el primer conteo de embeddings buscaba el texto "cuch" dentro del documento y daba **32** — pero 30 de esos eran reseñas de otros 8 lugares que dicen "cucharas" o "cuchillo". Borrarlos habría destruido los embeddings de Bairoletto, Batacazo y Cervecería OSS. Filtrando por `cmetadata->>'nombre'` eran exactamente 2.
- **Backup previo** en `backups/cuchi_2026-08-28.json` (173 KB, 370 filas), así que es reversible.
- **Los 43 `query_logs` que lo mencionan se dejaron intactos a propósito:** no son datos del lugar sino el registro histórico de lo que el sistema devolvió; borrarlos falsearía el historial y son material para la minería de queries pendiente.
- **Dato de contexto:** Google marcaba el lugar como *"Cerrado temporalmente"*, no permanentemente. El usuario confirmó que en Roca 89 ahora funciona otro local (Blest), o sea que la etiqueta de Google estaba desactualizada. **La etiqueta de Google falla en las dos direcciones**, y eso condiciona el diseño del detector de cerrados.

### 🔎 El scraper fallaba en silencio: `SIN_OPINIONES` tapaba fichas muertas de Google
- **Síntoma:** 11 lugares volvían `SIN_OPINIONES` semana tras semana, que se lee como "no tiene reseñas". No era eso: Damiana tiene **2316 reseñas en Google y nosotros 813**; Pizzería Alta Barda, 603 contra 39.
- **Diagnóstico:** la cantidad de fallos predecía exactamente el problema — 10 de los 11 tenían **7 fallos** (uno por semana, 7 semanas seguidas) y uno tenía 1. Abriendo las URLs a mano, las 10 con 7 fallos caen en el mapa genérico de Google y la que tenía 1 resuelve bien; un lugar de control (Ohana) también resuelve. Fallo determinista, no bot-detection ni rate limiting.
- **Causa:** cuando el place ID de una URL guardada deja de existir (el negocio cerró, o Google fusionó/reemplazó la ficha), Maps **no devuelve 404**: redirige a la portada del mapa. La URL queda con el nombre **vacío** entre las dos barras (`/maps/place//@-38.95,-68.06,11z/...`), sin `h1` y sin pestañas. Al no haber pestañas, `forzar_entrada_pestana_opiniones()` falla y el caso se archivaba como "no tiene pestaña de Opiniones".
- **Fix (repo `que-morfamos-scraper`):** `ficha_del_lugar_resolvio()` en `scraping_utils.py` detecta el redirect; `monitor_reviews.py` la usa **antes** de buscar la pestaña y devuelve `URL_MUERTA`, que viaja a `scraping_logs` con su propio nombre.
- **Y la otra mitad, que es la que lo hacía silencioso:** una ficha muerta no es un "error" (`procesar_lugar` vuelve por el camino normal), así que no entraba en ningún contador y el resumen del run no la mencionaba. Ahora el resumen imprime `Fichas muertas: N` con la lista, y `notificador.py` levanta ese número por regex y **lo manda a Discord**. Cadena verificada con un `run.log` simulado, con 10 y con 0.
- **Error propio en el camino, vale registrarlo:** el primer parche fue a `opiniones-scraper.py`, y el workflow semanal corre `monitor_reviews.py`. Peor: la función parcheada (`procesar_restaurante`, ~230 líneas) es **código muerto**, no la llama nadie.
- **Este es el mejor detector de cerrados disponible**, muy superior al silencio de reseñas: una ficha que Google dejó de resolver es una señal directa, no una inferencia. Pero **no significa "cerró" automáticamente** — Google también fusiona y reemplaza fichas — así que lo correcto es revalidar por nombre y dirección antes de dar de baja.

### 📊 Por qué el silencio de reseñas NO sirve como detector de cerrados (medido)
El usuario propuso "3 meses sin reseñas nuevas ya es señal". Los números dicen que no alcanza:

| señal | resultado |
|---|---|
| Sin reseñas hace +3 meses | **325 de 942 lugares (35% de la base)** |
| ...de esos, lugares chicos (<50 reseñas) | 215 — para ellos 3 meses de silencio es normal |
| Reseñas que mencionan cierre (Cuchí) | **1 de 315**, y de hace 17 meses |
| Lugares con menciones de cierre en 12 meses | 14, casi todos con 1 sola y varios abiertos |
| Cruzando ambas señales | 6 lugares — una cola de revisión manual, no una acción automática |

Y hay algo peor: **el silencio no significa "cerró", significa "dejamos de recibir datos"**, que tiene dos causas mezcladas. The Coffee Store, Mostaza y Armando Medialunas tienen su última reseña **exactamente el mismo día** (2026-01-08): tres lugares de 30-40 reseñas/mes no cierran juntos. `scraping_logs` ya sabe distinguirlo — silencio + `EXITO` es candidato a cierre; silencio + `SIN_OPINIONES`/`URL_MUERTA` es scraper roto.

### ⚡ El detalle se sirve en dos tiempos (pendiente #4 del 26-ago, resuelto)
- **Medición del endpoint:** metadata 0.31s, reseñas 1.13s, **análisis del LLM 4.49s**. El 72% de la espera es una sola parte y el resto está listo a 1.4s, pero se devolvía junto.
- **Fix:** `solo_base=1` devuelve todo menos el análisis. El frontend pide las dos cosas **en paralelo** (no en cadena: encadenarlas sumaría los tiempos en vez de solaparlos) y pinta la tarjeta apenas llega la base, dejando el esqueleto sólo en el bloque del resumen.
- **La respuesta base se cachea con clave propia que NO incluye el tono**, porque el tono sólo afecta al texto del LLM: un mismo lugar reusa su base entre los tres tonos.
- **Medido en el navegador con una tarjeta sin cachear:** 80ms esqueleto completo → **1441ms nombre, rating, dirección y 4 reseñas visibles** → 4000ms resumen completo. Contenido real a 1.4s en vez de 4.0s.

### 🔗 `url` expuesta en el detalle
Se agregó `url` al modelo **y a la consulta que carga `df_lugares`** — la columna existía en la tabla pero la query no la traía, así que el campo llegaba vacío al frontend. Se usa para linkear a la ficha de Google: horarios, teléfono y cómo llegar no los tenemos ni los queremos tener, y linkear devuelve tráfico a la fuente de los datos.

### ✅ Benchmark re-corrido tras los cambios de ranking
Esta sesión tocó código que alimenta el ranking (`variantes_de_concepto()` dejó de devolver duplicados, y esa lista es la que cuenta `evidencia` en `relevancia()`), así que se corrió el golden dataset completo antes de deployar en vez de razonar que "no debería afectar":

    22/22 PASARON (0 sin curar) | Recall@5=0.83 | Precision@5=0.84 | MRR=0.97 | Intent Accuracy=1.00

Contra la línea del 26-ago (`Recall 0.86 | Precision 0.80`): **sin regresión**. Pasan los 22 casos, MRR e Intent Accuracy quedan idénticos, y recall/precisión se movieron dentro de la variación entre corridas que ya estaba documentada — de hecho `0.83 / 0.84` coincide exacto con una corrida de v1 registrada más arriba. No se afirma que el retrieval "mejoró": los arreglos de esta sesión fueron sobre lo que el usuario **lee** (el detalle, el orden de reseñas, la frase destacada), no sobre qué lugares se recuperan.

**Nota operativa:** en Windows hace falta `PYTHONIOENCODING=utf-8` para correrlo redirigiendo la salida a un archivo. Sin eso revienta con `UnicodeEncodeError` porque la consola queda en cp1252 y el script imprime emojis.

### 🎨 Frontend (repo `que-morfamos-web`, resumen)
Sesión larga de producto. Lo relevante para el backend: nada rompe compatibilidad, y el frontend **degrada bien** si el backend no entiende `solo_base` (funciona como antes, sin la mejora). Lo demás: bienvenida rehecha como una escena de chat de grupo que se escribe sola (nombres y pedidos rotativos, con pedidos incompatibles que no pueden convivir), paleta de azul navy a ciruela oscuro para que acompañe al cartel de neón, hover del mapa al estilo Airbnb (destaca el pin, no mueve la cámara), rating en los marcadores, minimapa y link a Maps en el detalle, y fondo rehecho (el slideshow tenía un hueco negro de 10s por ciclo porque el CSS definía 5 turnos para 4 imágenes).

**Cierre de la sesión (últimos retoques del detalle):**
- **El minimapa ahora hace un zoom real, no un `scale`.** Pedido del usuario: "el zoom inicial es demasiado cercano y el zoom final debería mostrar la cuadra, o sea que se vean los nombres de las 2 calles más cercanas". No se podía con la implementación anterior: escalar tiles por CSS agranda píxeles, **no agrega detalle**, así que subir el `scale` sobre un mosaico de z14 sólo daba una imagen más borrosa. Se pasó a **dos mosaicos** —z13 para el arranque y z17 para el final— que se cruzan: el lejano crece de 1× a 16× y se desvanece justo cuando el cercano llegó a tamaño real (es lo que hace Leaflet al cambiar de nivel, pero en una pasada). Los pasos van de a ×2 (1‑2‑4‑8‑16) en vez de rampa lineal porque el zoom se percibe en escala logarítmica: interpolar linealmente de 1 a 16 se vive como un tirón al principio y un arrastre al final. Verificado moviendo la animación a mano: las dos capas quedan en lockstep exacto (lejos = 16 × cerca en todo el recorrido), así que el cruce no salta. Costo: 7 tiles por card en vez de 4.
- **Dirección y área en la misma línea**, con el botón de Google Maps debajo. Antes el botón quedaba *entre* los dos, partiendo al medio un dato que es uno solo ("dónde queda"). Medido después del cambio: dirección en y=401, área en y=402.
- **Se recorta `, Argentina` de las direcciones al mostrarlas** (`sinPais`, aplicado en modal, cards y detalle inline). En un sitio que sólo habla de Neuquén ese sufijo no desambigua nada y se comía media línea. El dato crudo en la base queda intacto.
- **El botón de cerrar del modal ya no se pisa con el minimapa** (se solapaban 21×21px en la esquina superior derecha; el botón está en `position: absolute` y no participaba del flujo).

**Nota de proceso:** `CI=true npx react-scripts build` pasó a ser obligatorio antes de cualquier push del frontend. Vercel corre el build con `CI=true`, que convierte los warnings de ESLint en errores — eso tuvo la producción atrasada ~10 commits sin ningún aviso visible, hasta que el usuario notó que no veía la paleta nueva.

---

### 📝 ESTADO PENDIENTE CONSOLIDADO (al cierre del 28-ago-2026)

**Estado del sistema (actualizado 01-sep):** `23/33 | Recall@5=0.54 | Precision@5=0.55 | MRR=0.69`.

⚠️ **Esa caída NO es una regresión: es que el benchmark dejó de mentir.** Hasta el 01-sep el dataset daba `22/22 | Recall 0.83 | Precision 0.84 | MRR 0.97`, pero sus casos de concepto eran `vegano`, `sin tacc` y `pelotero` — tres de los cinco únicos conceptos que tienen sinónimos curados. Estaba midiendo exactamente lo que funcionaba. Se agregaron **11 casos** (multiconcepto, conceptos no curados, ocasión y borde) con ground truth derivado por SQL de la base, y **los 10 que fallan son los 10 nuevos**: los 23 viejos siguen pasando. Cada caso nuevo apunta a un agujero real, no a una regresión.

Los números viejos siguen siendo válidos como referencia **del subconjunto viejo**; para comparar contra corridas anteriores al 01-sep hay que mirar esos 23 casos, no el titular.

#### 🔴 Alta prioridad

1. **Intentar una v5 de resúmenes que gane en retrieval, no sólo en honestidad.** Producción sigue con los resúmenes v1, que tienen **33 afirmaciones inventadas, 127 truncados a mitad de oración y 51 sin el párrafo de características** — pero v4 (que corrige todo eso) mide peor en retrieval y por eso no se promovió (ver la sección de las 4 iteraciones, arriba). Toda la infraestructura ya está construida y funcionando: `regenerar_resumenes.py` con shadow write, versionado por `resumen_prompt_version`, muestreo por característica, chequeo de groundedness, y `RESUMEN_COLUMN` en el backend para evaluar sin promover. Bumpear `PROMPT_VERSION` a v5 y regenerar cuesta ~$0.33 y ~13 min.
   - **Hipótesis a probar:** el problema de v4 no es que invente menos, es que dice **menos**. Los resúmenes v1 miden ~2000 chars contra ~1500 de v4 y enfatizan más las características, lo que da embeddings más ricos. Probar un prompt que exija **más detalle** en el párrafo 3 (enumerar todas las características que las reseñas confirmen, con el matiz de cada una) manteniendo la prohibición de inventar.
   - **Segunda vía, independiente:** v1 gana porque rankea mejor por *fuerza* de evidencia. Se podría dar esa señal explícitamente en vez de esperar que el embedding la infiera — ej. guardar por lugar cuántas reseñas respaldan cada característica (un JSONB `features_evidencia`) y usarlo en el ranking. Eso desacoplaría "qué features tiene" de "qué tan bien lo redactó el LLM".
   - **Criterio de aceptación:** correr el benchmark con `RESUMEN_COLUMN=resumen_reviews_v2` y `COLLECTION_NAME=reviews_embeddings_v2` y **superar** la línea de v1 (`22/22 | Recall 0.83 | Precision 0.84 | MRR 0.97`). Si no la supera, no promover.

2. **Locales cerrados: hay detector, falta el tratamiento.** *(actualizado 28-ago)* "Cuchí" ya se eliminó (con backup) y el scraper ahora detecta fichas muertas de Google y avisa por Discord — ver la sección del 28-ago. Lo que falta es qué hacer con ellas:
   - **Nunca borrar en duro.** Google se equivoca en las dos direcciones (marcaba a Cuchí como "temporalmente" cerrado cuando en realidad el local ya no existe) y además fusiona/reemplaza fichas. Un falso positivo que borra 315 reseñas no se deshace.
   - **Diseño acordado con el usuario:** dos columnas en `lugares` (`cerrado_at TIMESTAMPTZ`, `cerrado_tipo TEXT`) y el backend filtrando `WHERE cerrado_at IS NULL` al cargar `df_lugares` — una línea, y el lugar desaparece del bot sin destruir nada. Con dos salvaguardas: exigir la detección en **dos corridas semanales seguidas** antes de marcar (una falla de carga no puede matar un lugar vivo) y avisar por Discord para confirmación humana. Los embeddings sí se pueden borrar al confirmar, porque se regeneran.
   - **Pendiente inmediato:** los 10 lugares que ya tienen la URL muerta necesitan revalidación manual (buscar por nombre y dirección) para distinguir "cerró" de "Google le cambió la ficha".

3. **La búsqueda por nombre le erra al lugar cuando otro nombre lo contiene como substring.** *(nuevo, 28-ago — reportado por el usuario, causa CONFIRMADA offline)* Reproducido contra la base real sin gastar una sola llamada al LLM:
   - `"que tal es el tio"` → responde sobre **PERNILES Del Tío Rudy** (Butcher shop, 63 reseñas).
   - `"el tío"` → responde sobre **Antü RestoBar (Cipolletti)**.
   - **"El Tío" SÍ está en la base**, y es de los lugares más populares que tenemos: `Restaurant`, 4.4★, **3980 reseñas**, barrio NUEVO. O sea que no es un problema de cobertura de datos.
   - **Causa raíz: matching por substring sin límites de palabra, más un desempate por largo que va justo al revés.** En `detectar_mencion_exacta()` (`main.py:~2064`) los nombres se ordenan `key=len, reverse=True` y se acepta el primero que cumpla `nombre in query or query in nombre`. El orden largo-primero se agregó para que "827 Punto de Encuentro" le gane a "Encuentro" — y para esa dirección (`nombre in query`) está bien. Pero para la otra (`query in nombre`) es exactamente lo contrario de lo que hay que hacer: `"el tio"` está adentro de `"perniles d`**`el tio`** `rudy"`, y como ese nombre es más largo, **le gana al match exacto "El Tío"**. El chequeo de igualdad estricta existe (`if nombre_lower == q_norm`) pero vive en el segundo loop, **inalcanzable** porque el primero ya devolvió.
   - **Y sin límites de palabra, `"tio"` matchea `"pa`**`tio`**`"`**: los candidatos para `"tio"` son 11, entre ellos Antü RestoBar - **Patio** de Encuentro, El **Patio** de Franz, **PATIO** JUEZ, Bunker Bar **Patio** Cervecero y hasta Bakery and Confec**tio**nery Curri. El mismo bug está en `resumir_opiniones_local_gen()` (`main.py:~2238`, `.str.contains(q_clean, regex=False)`), que además desempata con `encontrados.sort()` **alfabético** — y "Antü…" es el primero del abecedario en esa lista. Eso explica exactamente por qué sale Antü.
   - **Fix candidato:** (a) probar igualdad normalizada **antes** que cualquier containment; (b) usar límites de palabra (``) en vez de substring desnudo; (c) para la dirección `query in nombre`, quedarse con el nombre **más corto** que la contenga (el match más ajustado), no con el más largo.
   - **Ojo al tocarlo:** el orden largo-primero está ahí por el caso "827 Punto de Encuentro" documentado el 23-ago. Cualquier cambio tiene que verificarse contra ese caso también, no sólo contra "El Tío".

4. **Los modificadores de estilo se descartan: "mejores pizzas", "pizzas estilo napolitano" y "pizzas napolitanas" devuelven los mismos 5 resultados.** *(nuevo, 28-ago — reportado por el usuario, cadena causal CONFIRMADA de punta a punta)*
   - **El router tira el modificador antes de que llegue al retrieval.** Medido con 3 llamadas aisladas a `analizar_query_semantica()` (no el pipeline entero):

         'mejores pizzas'           -> keywords: ['pizza']
         'pizzas estilo napolitano' -> keywords: ['pizza']
         'pizzas napolitanas'       -> keywords: ['pizza']

     Las tres colapsan a lo mismo. "napolitano" no sobrevive al primer paso.
   - **Y después el modo genérico remata:** como `"pizza"` está en `KEYWORD_TO_CATEGORIES` (`main.py:419`), `keywords_extra` queda vacío y `es_query_generica = True` (`main.py:~3186`). En ese modo `grupo_alta = candidatos_crudos` **sin ningún filtro por término** (`main.py:~2764`) y se ordena por popularidad: los mismos 5 de los 71 lugares con categoría Pizza, para cualquier consulta que mencione pizza.
   - **La señal SÍ está en los datos** (lugares cuyo `resumen_reviews` lo menciona / reseñas que lo mencionan): `a la piedra` 13/71, `masa madre` 15/97, `al molde` 3/25, `media masa` 2/20, `fugazza/fugazzeta` 6/55, `napolitana` 31/248. No falta información: falta usarla.
   - **⚠️ Trampa antes de "arreglarlo" con un match por keyword — "napolitana" significa tres cosas distintas en este corpus:**
     1. **103 de las 248 reseñas con "napolitan" hablan de *milanesa* a la napolitana**, no de pizza (*"Muy rica las milanesas napolitana"*). Un match ciego traería bodegones y parrillas a una búsqueda de pizzería.
     2. De las que sí son de pizza, la **"napolitana" argentina es una cobertura** (muzzarella + rodajas de tomate + ajo), no un estilo de masa. Verificado en las reseñas: *"poco morrón y sin rodajas de tomate en la napolitana"*, *"la pizza napolitana y americana"*.
     3. El **estilo napolitano italiano** (masa fina, alta hidratación, cornicione, horno a leña) es otra cosa más. O sea que `"pizzas estilo napolitano"` y `"pizzas napolitanas"` son **dos consultas diferentes** que hoy dan lo mismo — y ninguna de las dos se distingue de `"mejores pizzas"`.
   - **Fix candidato, en dos frentes:** (a) que el router **conserve los modificadores** en `keywords`/`synonyms` en vez de reducir todo al tipo de comida; (b) que `es_query_generica` **no se active** cuando la consulta trae un modificador, para que el filtro por evidencia corra. Empezar por los estilos de masa con señal limpia y sin ambigüedad (`a la piedra`, `masa madre`, `al molde`, `media masa`, `fugazzeta`); `napolitana` necesita desambiguar por contexto (pizza vs milanesa) y no es el caso por donde conviene arrancar.
   - **Alcance probablemente mayor que la pizza:** el mecanismo no tiene nada de específico de este caso — cualquier modificador sobre una palabra que esté en `KEYWORD_TO_CATEGORIES` debería colapsar igual. **Hipótesis sin medir**, vale la pena testearla con otras categorías antes de dimensionar el fix.

5. **El vocabulario de conceptos tiene techo: son 5, y el benchmark no puede verlo.** *(nuevo, 01-sep — reportado como "los conceptos andan de manera muy errática", MEDIDO)* No es errático: es sistemático. `SINONIMOS_CURADOS` tiene **5 conceptos** (`sin tacc`, `vegano`, `vegetariano`, `pelotero`, `celiaco`). Sobre 24 conceptos realistas que se probaron, **19 no tienen ninguna variante** y caen al match literal sobre `resumen_reviews`. Cuánta evidencia queda invisible (lugares cuyo resumen menciona el concepto):

         cerveza artesanal    57 literal  ->  458 con variantes  (x8.0)
         terraza              17          ->  100                (x5.9)
         pet friendly         20          ->   34                (x1.7)
         desayuno            147          ->  198                (x1.3)
         musica en vivo        0          ->   43   <- el termino literal no aparece en NINGUN resumen

   - **Y el golden dataset no puede detectarlo:** sus casos de concepto son `vegano`, `sin tacc` y `pelotero` — tres de los cinco curados. El dataset está sesgado exactamente hacia lo que funciona, por eso da 22/22 mientras el resto falla. **Ampliarlo con conceptos NO curados es requisito previo** a cualquier fix acá, porque hoy no hay con qué medir si mejora.
   - **~~Hallazgo aparte, de calidad de datos: `estacionamiento` no discrimina nada.~~ CORREGIDO el 01-sep: la afirmación era falsa.** Se había medido la ocurrencia **cruda** del término (747 de 929, 80%) en vez de lo que el código realmente usa. Contando sólo menciones **positivas** con `_mencion_positiva`, que es el criterio del ranking:

         termino           aparece   POSITIVAS
         pelotero            574        66  (11%)
         estacionamiento     747       153  (20%)
         vegano              293        58  (19%)
         sin tacc             97        82  (84%)
         terraza              17        16  (94%)

     Los resúmenes v1 sí enumeran características para casi todos los lugares, pero **la mayoría es para decir que NO las tienen**, y la conciencia de negación ya lo filtraba. `estacionamiento` discrimina bien: 153 lugares lo tienen de verdad. **Lección de método:** medir ocurrencia cruda cuando el código usa un matcher con conciencia de negación da conclusiones al revés.
   - **El techo del vocabulario sí se sostiene** al re-medirlo sobre menciones positivas: `cerveza artesanal` 56 → 439 (7.8×), `terraza` 16 → 89 (5.6×), `música en vivo` **0 → 43**, `pet friendly` 18 → 31 (1.7×).
   - **Fix candidato:** derivar las variantes de los datos en vez de curarlas a mano (vecinos en el espacio de embeddings, o una pasada offline del LLM sobre el vocabulario real de los resúmenes), y regenerarlas con cada scrape. Curar a mano es la misma deuda: dentro de un mes falta el concepto 26.

#### 🟡 Media prioridad (bugs acotados y deuda de documentación)

6. **Escribir `ARQUITECTURA_RAG.md`: la explicación de cómo funciona el motor, hoy.** *(nuevo, 28-ago — pedido por el usuario)* La lógica del RAG existe y funciona, pero **sólo está documentada como historia**: repartida en ~15 entradas cronológicas de esta bitácora, cada una describiendo un parche sobre el estado anterior. Para saber cómo funciona hoy hay que leer las 700 líneas **en orden** y aplicar mentalmente cada cambio encima del anterior. Eso ya se está pagando: diagnosticar los pendientes #3 y #4 exigió re-derivar el pipeline desde el código.
   - **La distinción que importa:** este `DEV_LOG.md` es un **diario** (qué pasó, en qué orden, por qué se decidió cada cosa) y conviene que siga siéndolo — es append-only y su valor es justamente el rastro de decisiones. Lo que falta es un **corte transversal**: un documento vivo que describa el estado **actual**, sin historia, que se actualiza en vez de crecer. Son dos artefactos distintos y mezclarlos es lo que volvió inusable la información.
   - **Esqueleto propuesto** (los números de línea son del 28-ago, van a moverse):
     1. **Entrada y router.** `/chat` vs `/chat/stream`, y `analizar_query_semantica()` (`:1801`) → `intencion` (RECOMMENDATION/SPECIFIC/FOLLOWUP/STATS/BLOCK), `tipo`, `target_name`, `keywords`, `synonyms`, `donde`. Aclarar que `intencion` **no mapea 1:1** al `mode` de la respuesta (FOLLOWUP no emite modo propio; BLOCK con contexto fresco da `general`, no `blocked`).
     2. **Resolución de nombre propio.** `detectar_mencion_exacta()` (`:1897`), el orden largo-primero y por qué está (caso "827 Punto de Encuentro"). Ver #3: hoy tiene un bug conocido acá.
     3. **Expansión de términos.** `SINONIMOS_CURADOS` (`:84`), `variantes_de_concepto()` (`:100`), `expandir_sinonimos()` (`:123`), el filtro `KEYWORDS_GENERICAS` (`:63`) y `_ordenar_terminos()` (determinismo: era un `set` y cambiaba entre corridas).
     4. **La bifurcación grande: Modo Genérico vs RAG.** `KEYWORD_TO_CATEGORIES` (`:389`) y la condición `es_query_generica` (`:~3186`). Es el punto de decisión menos evidente de todo el sistema y el que explica el pendiente #4.
     5. **Recuperación.** Vector search sobre `reviews_embeddings` + la Hybrid Injection sobre `resumen_reviews`, y por qué la inyección **no** corre en Modo Genérico.
     6. **Conciencia de negación.** `NEGATION_RE` (`:56`) y `_mencion_positiva()` (`:182`): por qué todo match por substring sobre texto de reseñas tiene que pasar por acá (*"lo único malo es que NO tienen parrilla"* contiene el término y significa lo contrario).
     7. **El ranking en cascada.** `relevancia()` (`:2778`) = `(conceptos, evidencia, calc_score)`. El docstring de esa función ya es la mejor explicación que hay en el repo — conviene levantarlo casi tal cual, con los casos que lo motivaron (pelotero, sin TACC).
     8. **El Juez LLM.** Qué valida, qué es `candidatos_confiables`/`skip_juez` y por qué lo inyectado por texto libre nunca se saltea el Juez.
     9. **Armado de cards.** El corte `exactos[:3] + relacionados[:2]` (el techo de 5 que confundió a la evaluación) y la elección de la frase destacada con evidencia.
     10. **Detalle de la card (`/restaurant`).** `get_keywords_from_topic()` (`:1408`), `rankear_reviews_por_topico()` (`:1460`), `obtener_reviews_por_local(terminos=…)` (`:702`), y las dos fases (`solo_base=1`).
     11. **Caché.** Prefijos versionados en Upstash Redis y cuándo hay que bumpearlos.
   - **Además de para nosotros, sirve para el portfolio.** Es un proyecto de portfolio: un documento que explique bien un pipeline RAG real —con las trampas y las decisiones, no el diagrama de manual— vale más que la mayoría de las features que se puedan agregar.
   - **Criterio de aceptación:** que alguien que nunca vio el repo pueda leerlo y después ubicar, sin abrir `main.py`, en qué eslabón cae un síntoma nuevo.

7. **UX/UI en mobile.** *(reportado el 01-sep, auditado a 375×812 y en gran parte RESUELTO el 01-sep)* El reporte fue "anda medio mal en cuanto a márgenes y scrolls". Medido, el problema no eran los márgenes sino **cuánta pantalla llegaba al contenido**, y por qué: las reservas de espacio estaban duplicadas y el layout de desktop se aplicaba tal cual a un teléfono.
   - ✅ **El banner ya no se mueve y el chat crece hacia abajo.** En la home el bloque header+chat se centraba verticalmente con `margin-top: auto` —pensado para desktop, donde el chat era chico y quedaban 475px de vacío—. En un teléfono el chat ocupa casi toda la pantalla, así que centrarlo empujaba el banner fuera del viewport: arrancaba en `y=19`, saltaba a `y=1` mientras se tipeaba la escena y en estados más largos llegaba a `y=-121`, entero fuera de pantalla **y sin forma de traerlo** (la página no scrollea, `.App` es `100dvh` con `overflow: hidden`).
     - **La pieza que faltaba era `min-height: 0` en `.messages-container`:** era el único eslabón de la cadena con `min-height: auto`, el valor inicial de un flex item, que le impide achicarse por debajo de su contenido. En vez de scrollear, el contenedor crecía y empujaba a sus hermanos. Vale recordarlo, porque el sintoma (“el header se va”) no apunta para nada a la causa.
   - ✅ **Reservas de espacio duplicadas.** `.messages-container` reservaba 84px para la barra de chips, que con resultados en pantalla **no se renderiza**. Y `.input-container` reservaba otros 75px para la barra flotante, que `.App.sidebar-layout` **ya reserva** con su `padding-bottom` — la misma barra contada dos veces. Ese padding era además 96px cuando la barra ocupa 75 desde el borde.
   - ✅ **El pie sale de mobile** y su contenido pasa al indicador de estado, que ahora es un botón con panel (cierra con Escape y al tocar afuera). Eran ~71px de alto permanente para un dato que se mira una vez.
   - ✅ **El header se lo lleva la identidad.** El logo tenía 56px de los 323 de la fila (17%) y los controles 262px (81%); peor, el video es 960×720 con `object-fit: contain` en una caja de 56×120, así que el cartel se dibujaba a **56×42** dejando 78px de alto vacío. Se plegó el selector de tono —**cuyo colapso ya estaba implementado en JS y nunca se le había escrito el CSS que lo esconde**, así que los tres tonos se mostraban siempre— y el logo pasó a **145×109**, 6,7 veces el área.
   - ✅ **Blancos de toque a 44×44** (Apple HIG / WCAG 2.5.5): el indicador de estado estaba en 26×26 y ahora además es interactivo, el mute en 34×34, los chips y el atajo de accesibilidad en 41px de alto.
   - ✅ **El scroller de chips ya avisa que sigue** (se ven 259px de 789 y quedaban cortados al medio, que se lee como algo roto) y **dejó de solaparse 9px** con el formulario.
   - ✅ **El placeholder entraba cortado** ("¿Qué tenés ganas de comer ho") y además "comer" dejaba afuera helado, café o birras. Ahora es "¿Qué te tienta hoy?". Se descartó achicar la tipografía del input: por debajo de 16px iOS hace zoom al enfocarlo, y eso ya estaba bien resuelto.
   - **Resultado medido a 375×812, antes → después:**

         banner en la home:      y=-121 (fuera de pantalla) -> y=1, y nunca se mueve
         chat con resultados:    276px (34% de la pantalla) -> 449px (55%)
         grilla de cards:        355px (44%) -> 453px (56%), y una card de 370px por fin entra entera
         huecos muertos:         204px -> 19px
         pie:                    117px -> 0
         logo dibujado:          56x42 -> 145x109

   - **Lo que queda abierto:** los botones de tono miden 45×36 **dentro** del selector (el selector plegado sí es un blanco de 44px, pero cada tono suelto no lo es cuando está expandido). Y la vista de "Lugares" sigue mostrando una card por vez: entra entera, pero no se ve la siguiente, así que no hay señal de que la lista siga.

8. **El contador del popup "despertando el servidor" se reiniciaba solo, y aparecía en el momento equivocado.** *(reportado y RESUELTO el 01-sep)* El contador iba `1, 0, 1, 0, 1, 2, 3, 0…` en vez de subir. **Causa:** los dos efectos de esos modales llevaban `messages` en las dependencias y ponían el contador en cero en el cuerpo; la escena de bienvenida agrega los mensajes mockeados de a uno con temporizadores, así que cada mensaje cambiaba la identidad del array, volvía a correr el efecto, limpiaba el `setInterval` y reiniciaba la cuenta. Como los mensajes llegan a intervalos irregulares, el patrón parecía aleatorio. `messages` se usaba sólo para calcular un booleano: ahora **ese booleano es la dependencia**, y el efecto corre únicamente cuando cambia.
   - Y el cambio de producto: los avisos ahora aparecen **sólo después de que el usuario toca la barra de escribir**. La intención se detecta en el `<form>` y no en el `onFocus` del input, porque el input está `disabled` justamente cuando el backend no responde —el único caso en que el aviso importa—, así que nunca habría recibido el foco.
   - Verificado rompiendo el health-check: 14s sin conexión y sin tocar la barra no aparece nada; al tocarla aparece y el contador baja 60→45 de corrido, con cero reinicios.

9. **El chat de bienvenida se rompe si el usuario escribe antes de que termine.** *(nuevo, 28-ago — frontend)* Reportado por el usuario y reproducido de casualidad durante la verificación de esta misma sesión: si se manda una consulta mientras la escena mockeada de WhatsApp todavía se está tipeando, el mensaje real del usuario queda **intercalado entre los mensajes inventados** y el del Sommelier se **fusiona** con el que sigue. La escena se agenda con una cadena de timers en el `useMemo`/efecto de `BIENVENIDA` (`src/App.js`) que sigue empujando mensajes al mismo array aunque ya haya conversación real. Fix candidato: cancelar la secuencia pendiente en cuanto el usuario envía (ya existe la bandera `cancelado` en el efecto, falta dispararla desde el submit) y decidir si los mensajes mock ya emitidos se limpian o se congelan.

10. **`exactos = locales_verificados[:3]`** (`main.py`, en `procesar_consulta_gen`) toma los 3 primeros aprobados por el Juez **en orden de lista, no por relevancia**. Se mitigó indirectamente mejorando el orden de `grupo_alta`, pero la causa sigue: si el Juez es permisivo, un lugar popular pero tangencial le gana el lugar a uno más específico. Confirmado con experimento controlado que es **preexistente**, no introducido por los fixes de esta sesión. Fix candidato: ordenar `locales_verificados` por alguna señal de calidad/especificidad antes de cortar a 3.

11. **El orden de "sin tacc": la sospecha anterior era INCORRECTA, y la causa real es otra.** *(medido el 01-sep)* Lo que decía este pendiente —"sospecha: `usa_match_count` queda en `False` cuando `es_query_generica` es `True`"— se comprobó **falso**: instrumentando la llamada, `"sin tacc"` da `es_query_generica: False`. Lo que pasa de verdad:
    - **El ranking funciona bien.** `grupo_alta_relevancia` sale `['Lucciana Pastelería Sin Gluten', 'Chachingo', 'Antares', …]` — el orden correcto, con Lucciana primera.
    - **Lo que se muestra es otro orden**, porque `exactos` conserva a propósito el orden en que el Juez devolvió los aprobados cuando la query es de **un solo concepto**. Eso está así por una medición previa: ordenar por relevancia en queries de un concepto bajaba el MRR de 1.00 a 0.33, porque con un solo concepto todos empatan en `conceptos` y el desempate cae en `evidencia`, que premia resúmenes verbosos y no lugares buenos.
    - **La distinción que falta:** `evidencia` es un mal desempate para conceptos de TIPO DE COMIDA ("parrilla", "pizza": mencionar más sinónimos sólo significa que el resumen es más largo) y un **buen** desempate para conceptos de REQUISITO EXCLUYENTE ("sin tacc", "vegano", "pet friendly": mencionar más sinónimos sí significa que el lugar realmente contempla esa necesidad — Lucciana matchea 4 términos de gluten, Antares 1). La regla actual los trata igual.
    - **Fix candidato:** distinguir los dos tipos de concepto y aplicar `evidencia` como desempate sólo a los excluyentes. Los excluyentes son pocos y ya están casi todos en `SINONIMOS_CURADOS`, así que se puede marcar ahí mismo con un flag.
    - ⚠️ **Dos intentos de arreglo, los dos descartados:**
      1. Reordenar `locales_verificados` por `grupo_alta_relevancia` **antes** del corte a 3. Revertido sin medir: pisa exactamente la decisión que la medición del MRR 1.00→0.33 dejó documentada.
      2. `usa_match_count = bool(filtro_terms)` en la rama de Modo Genérico, para que ordenara por la cascada en vez de por popularidad. **Medido y revertido:** el benchmark bajó de `Recall 0.83 / Precision 0.84` a `0.78 / 0.79`. Tres corridas previas dieron 0.83 de forma consistente y al revertir volvió a 0.83/0.84, así que la caída es real y no ruido. **Motivo probable:** en Modo Genérico la única keyword suele ser la categoría misma ("pizza", "vegano"), así que todos los candidatos empatan en `conceptos` y el desempate vuelve a caer en `evidencia` — el mismo efecto ya medido. Es decir: **el problema no es dónde se aplica la cascada, sino que `evidencia` es un mal desempate mientras no se distingan los dos tipos de concepto.** Ese es el fix real, y hay que hacerlo antes de volver a tocar dónde se ordena.

12. **Los ítems de "Lo mejor" se repiten entre sí.** *(nuevo, 28-ago)* Reportado por el usuario con un caso claro: para Cabildo Pizzería los tres bullets dicen lo mismo — "Excelentes pizzas!!!", "Pizza es esponjosa 100% y le colocan suficientes ingredientes", "Unas pizzas galácticas imperdibles". Tres formas de decir "las pizzas son ricas", cuando el bloque debería cubrir aspectos **distintos** (comida, ambiente, atención, precio).
   - **Causa probable, y probablemente agravada por esta misma sesión:** el prompt de `detail_topic` pide `{"positivos": ["p1","p2"]}` sin ninguna instrucción sobre que sean aspectos diferentes. Y ahora que las reseñas que se le muestran están filtradas por tema (ver Bug #1, arriba), las 5 que ve hablan **todas** de lo mismo — antes eran las más recientes, que mezclaban temas por casualidad y daban variedad por accidente. O sea: el fix de relevancia mejoró la fidelidad y de paso concentró la muestra.
   - **Fix candidato:** pedir explícitamente en el prompt que cada punto cubra un aspecto distinto y que no se repita la misma idea reformulada. Si eso no alcanza, mezclar la muestra: N reseñas del tema + M recientes cualesquiera, para que el modelo tenga de dónde sacar variedad.
   - **Cuidado al tocarlo:** este prompt ya fue arreglado esta sesión para no negar ausencias. Cualquier cambio tiene que verificarse contra ese caso también (Ohana + "opciones veganas"), no sólo contra la redundancia.

13. **El ranking no distingue el TIPO de local ni la ocasión.** *(nuevo, 28-ago)* Medido sobre la base: **78 lugares** son delivery/takeout (no son lugares para ir) y **285** son panadería, heladería, café o postres — casi el **39% de la base** no es un lugar para ir a comer un plato. `categoria` tiene 99.5% de cobertura, así que la señal existe. **No corresponde filtrarlos**: una panadería es la respuesta correcta para "dónde desayunar" y una heladería para "postres veganos". El problema es cuando la categoría **no coincide con lo que la consulta pide** (Lucciana es perfecta para "torta sin TACC" y equivocada para "almorzar sin TACC"). Fix candidato: un eslabón más en la cascada — `concepto > evidencia > coincidencia de ocasión > popularidad` — infiriendo de la consulta qué tipo de salida es. Delivery-only es el único caso tratable casi como regla global, con la salvedad de que "Takeout Restaurant" en Google a veces sólo significa que el lugar destaca el takeaway.

14. **Extracción de keywords en queries de queja.** Para `"la milanesa que comí ayer estaba una mierda, recomendame otra parrilla buena"` el router extrae "milanesa" como keyword pese a que el usuario se queja de eso y pide otra cosa. Quedó tapado por el fix de categorías, nunca resuelto de fondo. **Emparentado con el #4**: los dos son fallas de extracción de keywords del router —uno agrega lo que el usuario descartó, el otro descarta lo que el usuario pidió— así que conviene atacarlos en la misma pasada sobre ese prompt.

#### 🔵 Baja prioridad / housekeeping

15. **`DEEPSEEK_MODEL` por defecto apunta a un ID viejo:** `deepseek-chat` ya no figura en el catálogo de DeepSeek (ahora `deepseek-v4-flash`/`v4-pro`). Sólo importa si se vuelve a ese proveedor; el default está en `main.py` y es overrideable por env.

16. **LangSmith desactualizado.** No se corrió `run_langsmith_eval.py` desde los cambios del 25/26-ago, así que el historial trackeado no refleja el 22/22 actual ni el cambio de proveedor. Correrlo deja la comparación "antes/después" registrada.

17. **Minar `query_logs` para ampliar el golden dataset.** La tabla y el logging están activos en producción desde el 24-ago pero no se usaron todavía. Esperar a que se acumulen queries reales y revisarlas para sumar casos nuevos (o más realistas). Sin fecha ni umbral definido: "volver en unas semanas".

18. **Tab de LangSmith en el dashboard (`que-morfamos-dashboard`).** Evaluado y pospuesto: por ahora alcanza con la UI propia de LangSmith (`smith.langchain.com`, proyecto `que-morfamos`, dataset `que-morfamos-golden` → Experiments). Retomar sólo si se quiere todo centralizado en el portfolio.

19. **Housekeeping del repo.** *(actualizado 28-ago, parcialmente resuelto)* ✅ `__pycache__/main.cpython-314.pyc` se destrackeó (`git rm --cached`) y ✅ `backups/` — que guarda el respaldo de Cuchí **con reseñas de usuarios** — ya está en `.gitignore`, junto con `temp.txt` y `.claude/`. **Queda pendiente:** en `que-morfamos-scraper`, `procesar_restaurante()` en `opiniones-scraper.py` (~230 líneas) es código muerto — el camino vivo es `monitor_reviews.py`.

#### ⚠️ Nota operativa sobre costos
Producción corre sobre OpenAI con saldo acotado (~$4 al 26-ago). `AI_PROVIDER=openai-mini` (gpt-4o-mini para ambos LLMs) rinde ~1200 consultas; `openai` (mini + gpt-4o) rinde ~70. No cambiar a `openai` sin recargar saldo. Ver la comparación de proveedores más arriba para las alternativas evaluadas y por qué se descartaron.
