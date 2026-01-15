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

---
*Bitácora iniciada automáticamente por Antigravity Agent.*
