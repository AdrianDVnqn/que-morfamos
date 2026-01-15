# Qué Morfamos - Backend API

API de recomendaciones gastronómicas para [quemorfamos.adriandv.dev](https://quemorfamos.adriandv.dev), que combina búsqueda semántica con generación de respuestas en lenguaje natural.

## Demo en Vivo

- **Frontend:** [quemorfamos.adriandv.dev](https://quemorfamos.adriandv.dev)
- **API:** Deployada en Fly.io

## Descripción

El backend procesa consultas en lenguaje natural del tipo "quiero comer sushi cerca del río" y:

1. Detecta la intención del usuario (recomendación, estadísticas, consulta específica)
2. Realiza búsqueda semántica sobre embeddings de ~937 restaurantes
3. Filtra y rankea resultados usando un LLM como juez
4. Genera respuestas contextualizadas con diferentes tonos de personalidad

## Características

- Búsqueda semántica con embeddings (OpenAI text-embedding-3-small)
- Streaming de respuestas (NDJSON)
- Múltiples tonos de personalidad (cordial, soberbio, irónico)
- Caché de embeddings y respuestas
- Rate limiting y logging de queries
- Health checks para cold start

## Contexto del Proyecto

Este desarrollo forma parte de mi portfolio personal, enfocado en aplicar conocimientos de NLP, sistemas RAG y desarrollo de APIs. Integra conceptos de:

- Retrieval-Augmented Generation (RAG)
- Embeddings semánticos y búsqueda vectorial
- APIs con streaming (Server-Sent Events / NDJSON)
- Prompt engineering y LLM orchestration
- Deploy en contenedores (Docker + Fly.io)

## Stack Tecnológico

| Componente | Tecnología |
|------------|------------|
| Framework | FastAPI |
| LLM | OpenAI GPT-4o / DeepSeek |
| Embeddings | OpenAI text-embedding-3-small |
| Vector Store | LangChain PGVector (Supabase) |
| Base de Datos | Supabase (PostgreSQL) |
| Deploy | Fly.io (Docker) |

## Endpoints Principales

| Endpoint | Método | Descripción |
|----------|--------|-------------|
| `/chat` | POST | Chat tradicional (respuesta completa) |
| `/chat/stream` | POST | Chat con streaming (NDJSON) |
| `/restaurant/{nombre}` | GET | Detalle de un restaurante |
| `/health` | GET | Health check para warmup |
| `/stats` | GET | Estadísticas generales |

## Instalación Local

```bash
# Clonar el repositorio
git clone https://github.com/AdrianDVnqn/que-morfamos.git
cd que-morfamos

# Crear entorno virtual
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
.venv\Scripts\activate     # Windows

# Instalar dependencias
pip install -r requirements.txt

# Configurar variables de entorno
cp mis_claves.env.example mis_claves.env
# Editar mis_claves.env con tus API keys

# Iniciar servidor
uvicorn main:app --reload
```

## Variables de Entorno

```env
DATABASE_URL=postgresql://...
OPENAI_API_KEY=sk-...
DEEPSEEK_API_KEY=sk-...
```

## Arquitectura RAG

```
┌─────────────────────────────────────────────────────────────┐
│                      USER QUERY                             │
│              "Donde puedo comer sushi?"                     │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                   INTENT DETECTION                          │
│         ¿Recomendación? ¿Estadística? ¿Detalle?            │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                  SEMANTIC SEARCH                            │
│     Query embedding → PGVector similarity search            │
│              Top K candidatos (~20)                         │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                    LLM JUDGE                                │
│      Filtrar candidatos por relevancia semántica            │
│           Exactos (4) + Relacionados (3)                    │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                RESPONSE GENERATION                          │
│     Streaming con contexto, tono y personalidad             │
└─────────────────────────────────────────────────────────────┘
```

## Repositorios Relacionados

- **que-morfamos** (este repo): Backend API FastAPI
- **que-morfamos-web**: Frontend React
- **que-morfamos-scraper**: Pipeline de datos y embeddings
- **que-morfamos-dashboard**: Panel de monitoreo Next.js

## Disclaimer

Este proyecto fue desarrollado exclusivamente con fines educativos y de aprendizaje personal. No tiene propósitos comerciales ni se obtiene rédito económico de él. El código se comparte públicamente como parte de mi portfolio profesional.
