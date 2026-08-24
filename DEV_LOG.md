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

---
*Bitácora actualizada por Antigravity Agent (v7.1).*
