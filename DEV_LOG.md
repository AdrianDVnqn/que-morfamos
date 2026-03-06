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

#### 5. Configuración de Infraestructura
- **Fly.toml:** Se revirtió la configuración a **Tier Gratuito** (`min_machines_running = 0`, `memory_mb = 768`) optimizando el código para que sea estable bajo estas restricciones de hardware.

---
*Bitácora actualizada por Antigravity Agent.*
