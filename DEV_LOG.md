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
- **IP Geolocation:** Se corrigió la URL de la API `theipapi.com` que estaba mal formada, permitiendo geolocalizar usuarios nuevamente.
- **Dashboard Legacy:** Se modificó el scraper `monitor_reviews.py` para escribir también en la tabla `scraping_logs`, reviviendo el gráfico de actividad histórico que había dejado de funcionar el 10/01.

#### 6. Despliegue a Producción
- **Acción:** Deploy manual a **Fly.io** (`fly deploy`) para reflejar todos estos cambios en el entorno productivo, corrigiendo la versión obsoleta del 8 de Enero.

---
*Bitácora iniciada automáticamente por Antigravity Agent.*
