import os
import json
import re
import unicodedata
import asyncio
import logging
import math
from contextlib import asynccontextmanager
import pandas as pd
import numpy as np
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_pinecone import PineconeVectorStore
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from upstash_redis import Redis

# ==========================================
# 0. CONFIGURACIÓN DE LOGS
# ==========================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("QueMorfamos")

# ==========================================
# 1. CONFIGURACIÓN DE ENTORNO
# ==========================================
PINECONE_INDEX_NAME = os.getenv("PINECONE_INDEX_NAME", "que-morfamos-nqn")
ARCHIVO_DATASET = "dataset_reviews.parquet"
UPSTASH_REDIS_REST_URL = os.getenv("UPSTASH_REDIS_REST_URL")
UPSTASH_REDIS_REST_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN")

class RedisCacheManager:
    def __init__(self, url, token):
        self.client = None
        if url and token:
            try:
                self.client = Redis(url=url, token=token)
                logger.info("✅ Redis conectado.")
            except Exception as e:
                logger.error(f"⚠️ Error Redis: {e}")

    def _sanitize_key(self, key):
        if not key: return "unknown"
        return str(key).lower().strip().replace(" ", "_")

    def get_json(self, prefix, key):
        if not self.client: return None
        full_key = f"{prefix}:{self._sanitize_key(key)}"
        try:
            data = self.client.get(full_key)
            if data: return data if isinstance(data, dict) else json.loads(data)
            return None
        except: return None

    def set_json(self, prefix, key, value_dict, expire=604800):
        if not self.client: return
        full_key = f"{prefix}:{self._sanitize_key(key)}"
        try:
            json_str = json.dumps(value_dict, ensure_ascii=False)
            self.client.set(full_key, json_str, ex=expire)
        except: pass
    
    def set_value(self, key, value, expire=None):
        if not self.client: return
        try:
            self.client.set(key, value, ex=expire)
        except: pass
        
    def get_value(self, key):
        if not self.client: return None
        try:
            return self.client.get(key)
        except: return None

cache = RedisCacheManager(UPSTASH_REDIS_REST_URL, UPSTASH_REDIS_REST_TOKEN)

# Variables globales
df = None
vectorstore = None
llm_mini = None  
llm_smart = None 

# ==========================================
# 2. LIFESPAN
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    global df, vectorstore, llm_mini, llm_smart
    logger.info("☁️ Iniciando servidor (Lifespan v6.8)...")
    
    if os.path.exists(ARCHIVO_DATASET):
        try:
            df = pd.read_parquet(ARCHIVO_DATASET)
            cols = ['restaurante', 'texto', 'direccion', 'barrio', 'zona', 'autor', 'fecha']
            for col in cols:
                if col in df.columns:
                    df[col] = df[col].fillna("").astype(str).str.strip()
            df['rating_gral'] = pd.to_numeric(df['rating_gral'], errors='coerce').fillna(0.0)
            
            def _norm(s):
                if pd.isna(s) or s is None: return ""
                t = str(s).lower().strip()
                t = unicodedata.normalize('NFD', t)
                t = ''.join(ch for ch in t if unicodedata.category(ch) != 'Mn')
                t = re.sub(r'[^\w\s]', '', t)
                return t
            df['restaurante_ascii'] = df['restaurante'].apply(_norm)
            df['texto_ascii'] = df['texto'].apply(_norm)
            logger.info(f"✅ DataFrame cargado: {len(df)} filas.")
        except Exception as e:
            logger.error(f"❌ Error Parquet: {e}")
            df = pd.DataFrame()
    else:
        df = pd.DataFrame()

    try:
        embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
        vectorstore = PineconeVectorStore.from_existing_index(
            index_name=PINECONE_INDEX_NAME, embedding=embeddings
        )
        
        provider_mode = os.getenv("AI_PROVIDER", "hybrid").lower()
        deepseek_key = os.getenv("DEEPSEEK_API_KEY")
        openai_key = os.getenv("OPENAI_API_KEY")

        logger.info(f"🤖 Configurando LLMs en modo: {provider_mode.upper()}")

        openai_mini = ChatOpenAI(model="gpt-4o-mini", temperature=0, api_key=openai_key, max_tokens=1024)
        openai_smart = ChatOpenAI(model="gpt-4o", temperature=0, api_key=openai_key, max_tokens=1024)
        
        ds_instance = None
        if deepseek_key:
            ds_instance = ChatOpenAI(
                model="deepseek-chat", 
                openai_api_key=deepseek_key, 
                openai_api_base="https://api.deepseek.com",
                temperature=0,
                max_tokens=1024
            )

        if provider_mode == "deepseek":
            if not ds_instance: raise ValueError("Falta DEEPSEEK_API_KEY")
            llm_mini = ds_instance
            llm_smart = ds_instance
        elif provider_mode == "openai":
            llm_mini = openai_mini
            llm_smart = openai_smart
        else: # hybrid
            llm_mini = openai_mini
            llm_smart = ds_instance if ds_instance else openai_smart

        logger.info(f"✅ IA lista. Mini: {llm_mini.model_name} | Smart: {llm_smart.model_name}")

    except Exception as e:
        logger.error(f"❌ Error iniciando LLMs: {e}")
        llm_mini = ChatOpenAI(model="gpt-4o-mini", temperature=0)
        llm_smart = ChatOpenAI(model="gpt-4o-mini", temperature=0)

    yield
    logger.info("🛑 Apagando servidor...")

# ==========================================
# 3. APP & MODELS
# ==========================================
app = FastAPI(title="Que Morfamos API (Semantic)", version="6.8.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class RestaurantCard(BaseModel):
    nombre: str
    rating: float = 0
    total_reviews: int = 0
    direccion: str = ""
    barrio: str = ""
    zona: str = ""
    descripcion: str = ""
    frase_destacada: str = ""
    autor_reseña: str = ""

class ReviewDetail(BaseModel):
    autor: str
    rating: int
    texto: str
    fecha: str

class RestaurantDetail(BaseModel):
    nombre: str
    rating: float
    total_reviews: int
    direccion: str
    barrio: str
    zona: str
    lat: float = 0
    lng: float = 0
    resumen_general: str
    aspectos_positivos: List[str] = []
    aspectos_negativos: List[str] = []
    reviews: List[ReviewDetail] = []

class QueryRequest(BaseModel):
    query: str
    conversation_context: Optional[dict] = None
    tone: Optional[str] = None

class QueryResponse(BaseModel):
    response: str
    mode: str
    conversation_context: Optional[dict] = None
    locations: list = []
    restaurant_cards: List[RestaurantCard] = []
    detail_content: str = ""

# --- HELPERS SAFE ---
def safe_str(val):
    if pd.isna(val) or val is None: return ""
    return str(val).strip()

def safe_float(val):
    try: return float(val) if pd.notna(val) else 0.0
    except: return 0.0

def safe_int(val):
    try: return int(float(val)) if pd.notna(val) else 0
    except: return 0

def formatear_autor(nombre):
    nombre = safe_str(nombre)
    if not nombre or nombre.lower() == "nan": return "Anónimo"
    partes = nombre.split()
    return f"{partes[0]} {partes[1][0]}." if len(partes) > 1 else partes[0]

def fecha_a_orden(fecha_str):
    fecha_str = safe_str(fecha_str).lower()
    if not fecha_str: return 9999
    numeros = re.findall(r'\d+', fecha_str)
    num = int(numeros[0]) if numeros else 1
    if 'hora' in fecha_str: return num
    if 'día' in fecha_str: return num * 24
    if 'semana' in fecha_str: return num * 168
    if 'mes' in fecha_str: return num * 720
    if 'año' in fecha_str: return num * 8760
    return 5000

def obtener_coordenadas(nombres, df):
    locs = []
    for nom in nombres:
        if not nom: continue
        mask = df['restaurante'].str.lower() == nom.lower()
        if mask.any():
            r = df[mask].iloc[0]
            lat = safe_float(r.get('latitud'))
            lng = safe_float(r.get('longitud'))
            if lat != 0 and lng != 0:
                locs.append({
                    "nombre": safe_str(r['restaurante']),
                    "lat": lat, "lng": lng,
                    "direccion": safe_str(r.get('direccion')),
                    "rating": safe_float(r.get('rating_gral')),
                    "total_reviews": safe_int(r.get('total_reviews_google'))
                })
    return locs

def sanitize_tone(t):
    if not t: return 'cordial'
    tt = str(t).lower().strip()
    return tt if tt in {'cordial', 'soberbio', 'sassy'} else 'cordial'

def tone_system_instruction(tone):
    tone = sanitize_tone(tone)
    
    # 1. Base sólida: Define identidad y "anti-reglas" para evitar clichés
    base = (
        "Rol: Sos un experto crítico gastronómico de Neuquén Capital. "
        "Conocés la ciudad como la palma de tu mano.\n"
        "Idioma: Español Rioplatense (Argentino) NATURAL.\n"
        "Reglas de Estilo:\n"
        "- NO abuses del 'Che' ni del 'Viste' (usalo solo si fluye).\n"
        "- NO uses jerga forzada (evitá 'chabón', 'pibe' salvo que cuadre perfecto).\n"
        "- Cuando des una opinión, fundaméntala con datos de las reseñas.\n"
    )

    # 2. Personalidades con matices específicos
    if tone == 'cordial': 
        return (
            f"{base}\n"
            "Personalidad: Amigable, servicial y empático. Como ese amigo que siempre te tira la posta con buena onda.\n"
            "Objetivo: Que el usuario se sienta bienvenido y encuentre lo que busca sin vueltas."
        )
    
    if tone == 'soberbio': 
        return (
            f"{base}\n"
            "Personalidad: 'Tincho' de clase alta, snob gastronómico y levemente pedante.\n"
            "Estilo: Usá palabras como 'básico', 'pretencioso', 'top', 'exclusive'. "
            "Mirás un poco por encima del hombro a los lugares comunes, pero reconocés la calidad cuando la ves.\n"
            "Ejemplo: 'O sea, si te gusta la comida recalentada, allá vos... pero yo iría a otro lado'."
        )
    
    if tone == 'sassy': 
        return (
            f"{base}\n"
            "Personalidad: Irónico, picante y sin filtro. Tenés un humor ácido.\n"
            "Estilo: Tirá 'shade' (sarcasmo) con elegancia. Si un lugar es malo, destruilo con creatividad. "
            "Si es bueno, decilo pero con un toque de incredulidad ('Mirá vos, al fin uno que zafa')."
        )

    # Default (Fallback)
    return f"{base}\nPersonalidad: Profesional, claro y directo. Priorizá la información útil sobre el estilo."

# ==========================================
# 4. LÓGICA DE NEGOCIO Y FILTROS
# ==========================================

# === NUEVO: FILTRO HARDCODED PARA SEGURIDAD EXTREMA ===
def check_keyword_ban(query):
    """
    Retorna TRUE si la query contiene palabras prohibidas explícitas.
    Esto bypassea al LLM para asegurar bloqueo de términos graves.
    """
    banned_words = [
        "travesti", "travestis", "travesaño", "travesaños", "teta", "tetas", "culo", "pito", "pene", "pija", "pijas",
        "sexo", "puta", "puto", "concha", "verga", "porno", "xxx", 
        "droga", "cocaina", "marihuana", "falopa", "merca", "porro"
    ]
    q_norm = query.lower()
    for word in banned_words:
        # Buscamos la palabra exacta o rodeada de espacios/signos
        pattern = r'(?<!\w)' + re.escape(word) + r'(?!\w)' 
        # Pero para plurales simples como 'tetas' o 'travestis' la lista ya los tiene
        if word in q_norm:
            return True
    return False

def get_keywords_from_topic(topic):
    if not topic: return []
    stopwords = {
        "de", "la", "el", "en", "y", "que", "los", "las", "un", "una", "del", "para", "con", 
        "donde", "hay", "lugar", "lugares", "comer", "mejor", "mejores", "neuquen",
        "que", "qué", "tal", "como"
    }
    words = safe_str(topic).lower().split()
    clean_words = [w for w in words if w not in stopwords and len(w) > 2]
    stemmed_words = []
    for w in clean_words:
        if w.endswith('es') and len(w) > 4: stemmed_words.append(w[:-2])
        elif w.endswith('s') and len(w) > 3: stemmed_words.append(w[:-1])
        else: stemmed_words.append(w)
    return stemmed_words

def rankear_reviews_por_topico(df_reviews, topic=None):
    df_local = df_reviews.copy()
    df_local['orden_fecha'] = df_local['fecha'].apply(fecha_a_orden)
    if 'rating_user' in df_local.columns:
        df_local['rating_user'] = pd.to_numeric(df_local['rating_user'], errors='coerce').fillna(0).astype(int)
    else:
        df_local['rating_user'] = 0

    if not topic or len(topic) < 3:
        return df_local.sort_values(['orden_fecha', 'rating_user'], ascending=[True, False])

    keywords = get_keywords_from_topic(topic)
    if not keywords:
        return df_local.sort_values('orden_fecha')

    def calcular_relevancia(row):
        texto = safe_str(row.get('texto')).lower()
        rating = row.get('rating_user', 0)
        score = 0
        match_found = False
        for k in keywords:
            if k in texto:
                score += 100 
                match_found = True
        if match_found:
            if rating >= 4: score += 50 
            elif rating <= 2: score -= 20 
        return score

    df_local['score_topic'] = df_local.apply(calcular_relevancia, axis=1)
    if df_local['score_topic'].max() == 0:
        return df_local.sort_values('orden_fecha')
    return df_local.sort_values(['score_topic', 'orden_fecha'], ascending=[False, True])

def seleccionar_mejor_review(df_local, topic_query=None):
    sorted_df = rankear_reviews_por_topico(df_local, topic_query)
    if sorted_df.empty: return None
    if topic_query:
        top_match = sorted_df.iloc[0]
        if top_match['score_topic'] > 0:
            if len(safe_str(top_match['texto'])) >= 4: return top_match
        return None 
    candidatas = sorted_df[sorted_df['texto'].str.len() > 25] 
    if not candidatas.empty: return candidatas.iloc[0]
    return sorted_df.iloc[0]

async def generar_descripcion_async(llm, nombre, sample, tone='cordial'):
    try:
        prefix = tone_system_instruction(tone)
        prompt = (
            f"{prefix}\n"
            f"CONTEXTO: El restaurante '{nombre}' queda en NEUQUÉN CAPITAL.\n"
            f"Basado en: {sample}\n"
            "Describe el lugar en máx 15 palabras atractivas.\n"
            "IMPORTANTE: NO menciones 'Buenos Aires'."
        )
        res = await llm.ainvoke(prompt)
        return res.content.strip().replace('"','')
    except:
        return "Restaurante popular en Neuquén."
    
    
def obtener_reviews_tematicas(df_local, keywords, limit=8):
    """
    Busca en TODAS las reviews del local aquellas que contengan las keywords.
    """
    if not keywords: return df_local.head(limit)
    
    # Creamos un patrón Regex: (pelotero|juegos|niños)
    # Re.escape asegura que caracteres raros no rompan el regex
    pattern = '|'.join([re.escape(k) for k in keywords if len(k) > 2])
    
    if not pattern: return df_local.head(limit)
        
    mask = df_local['texto'].str.lower().str.contains(pattern, regex=True, na=False)
    reviews_tematicas = df_local[mask]
    
    if not reviews_tematicas.empty:
        # Priorizamos estas reviews específicas
        return reviews_tematicas.head(limit)
    else:
        # Si no hay mención explícita, devolvemos las generales
        return df_local.head(limit)

async def obtener_restaurant_cards(nombres_restaurantes, df, llm, query_context=None, tone='cordial', strict_mode=True, keywords_list=None, synonyms_list=None):
    cards = []
    tasks = [] 
    
    # 1. Armamos la lista completa de términos de búsqueda
    search_terms = set()
    if keywords_list:
        for k in keywords_list: search_terms.add(k.lower())
    if synonyms_list:
        for s in synonyms_list: search_terms.add(s.lower())
    
    # Si la lista vino vacía, usamos la query original
    if not search_terms and query_context:
        search_terms.add(query_context.lower())

    # Filtramos palabras muy cortas
    final_search_terms = [k for k in search_terms if len(k) > 3]

    for nombre in nombres_restaurantes:
        if not nombre: continue
        mask = df['restaurante'].str.lower() == nombre.lower()
        if mask.any():
            rest_df = df[mask]
            row = rest_df.iloc[0]
            nombre_real = safe_str(row['restaurante'])

            # 2. BÚSQUEDA DE EVIDENCIA
            # Aquí es donde encontramos las reseñas de "pelotero"
            if final_search_terms:
                reviews_filtradas = obtener_reviews_tematicas(rest_df, final_search_terms, limit=8)
                
                if not reviews_filtradas.empty:
                    # Unimos hasta 3000 chars para asegurar contexto completo
                    sample_text = " ... ".join([safe_str(t) for t in reviews_filtradas['texto']])[:3000]
                    best_review = reviews_filtradas.iloc[0]
                else:
                    sample_text = " ".join([safe_str(t) for t in rest_df['texto'].head(5)])[:1000]
                    best_review = rest_df.iloc[0]
            else:
                sample_text = " ".join([safe_str(t) for t in rest_df['texto'].head(5)])[:1000]
                best_review = rest_df.iloc[0]

            frase = safe_str(best_review['texto'])[:200] + "..."
            autor = formatear_autor(best_review.get('autor'))

            # 3. GENERACIÓN CON LLM
            # Cache key única por tema de búsqueda
            topic_key = "-".join(sorted(list(search_terms))) if search_terms else "general"
            cache_key = f"desc_{nombre_real}_{topic_key}_{sanitize_tone(tone)}"
            
            desc = cache.get_json("desc", cache_key)
            
            if desc:
                tasks.append({"type": "cached", "val": desc, "row": row, "frase": frase, "autor": autor, "nombre_real": nombre_real})
            else:
                # El prompt le dice al LLM qué buscar
                contexto_extra = f"El usuario busca conceptos relacionados con: '{', '.join(final_search_terms)}'. Si aparecen en las reviews, MENCIONALO."
                
                # Reutilizamos la función helper que ya tenías o la definimos abajo
                task_coro = generar_descripcion_async_tematica(llm, nombre_real, sample_text, tone, contexto_extra)
                tasks.append({"type": "generate", "val": task_coro, "row": row, "frase": frase, "autor": autor, "nombre_real": nombre_real, "cache_key": cache_key})

    # Ejecución Async
    generations_needed = [t['val'] for t in tasks if t['type'] == 'generate']
    if generations_needed:
        results = await asyncio.gather(*generations_needed)
    
    gen_idx = 0
    for item in tasks:
        if item['type'] == 'cached':
            descripcion = item['val']
        else:
            descripcion = results[gen_idx]
            gen_idx += 1
            cache.set_json("desc", item['cache_key'], descripcion)

        cards.append(RestaurantCard(
            nombre=item['nombre_real'],
            rating=safe_float(item['row'].get('rating_gral')),
            total_reviews=safe_int(item['row'].get('total_reviews_google')),
            direccion=safe_str(item['row'].get('direccion')),
            barrio=safe_str(item['row'].get('barrio')),
            zona=safe_str(item['row'].get('zona')),
            descripcion=safe_str(descripcion),
            frase_destacada=safe_str(item['frase']),
            autor_reseña=safe_str(item['autor'])
        ))
    
    return cards

# Helper para el prompt de descripción
async def generar_descripcion_async_tematica(llm, nombre, sample, tone, contexto_extra):
    try:
        prefix = tone_system_instruction(tone)
        prompt = (
            f"{prefix}\n"
            f"CONTEXTO: Restaurante '{nombre}' en NEUQUÉN.\n"
            f"{contexto_extra}\n"
            f"Reviews de evidencia: {sample}\n\n"
            "TAREA: Describe el lugar en 1 oración atractiva (máx 20 palabras).\n"
            "Si las reviews confirman que tiene lo que busca el usuario (ej: pelotero), CONFIRMALO en la descripción."
        )
        res = await llm.ainvoke(prompt)
        return res.content.strip().replace('"','')
    except:
        return f"Restaurante popular en Neuquén: {nombre}."

def obtener_restaurant_cards_simple(nombres_restaurantes, df):
    cards = []
    for nombre in nombres_restaurantes:
        if not nombre: continue
        mask = df['restaurante'].str.lower() == nombre.lower()
        if mask.any():
            row = df[mask].iloc[0]
            cards.append(RestaurantCard(
                nombre=safe_str(row['restaurante']),
                rating=safe_float(row.get('rating_gral')),
                total_reviews=safe_int(row.get('total_reviews_google'))
            ))
    cards.sort(key=lambda x: x.rating, reverse=True)
    return cards

# ==========================================
# 5. INTENCIÓN Y DETECCIÓN (BRAIN)
# ==========================================

async def analizar_query_semantica(query, llm):
    """ USA LLM_SMART. Retorna: {tipo, keywords, synonyms} """
    q_lower = query.lower()
    
    # Bypass para evitar falsos positivos de seguridad, 
    # pero igual dejamos que el LLM genere los sinónimos.
    is_safe_bypass = False
    whitelist = ["helad", "crema", "pelotero", "juego", "niñ", "chic", "infantil"]
    for safe in whitelist:
        if safe in q_lower: is_safe_bypass = True

    cache_key = f"analysis_v82_{q_lower.strip()}"
    cached = cache.get_json("analysis", cache_key)
    if cached: return cached

    template = """
    Analiza la intención del usuario. Query: "{query}"
    
    1. SEGURIDAD:
       - Si es sexual/insulto -> "BLOCK".
       - Excepciones: Comida/Infantil es seguro.
    
    2. CLASIFICACIÓN: "PRODUCTO" (Pizza) o "VIBE" (Pelotero, Romántico).
    
    3. EXTRAER KEYWORDS Y SINÓNIMOS (CRÍTICO):
       - "keywords": La palabra exacta buscada (singular).
       - "synonyms": Lista de 3 o 4 palabras relacionadas semánticamente que sirvan para buscar evidencia en reseñas.
       
       EJEMPLOS:
       - Query: "Lugar con pelotero" -> keywords: ["pelotero"], synonyms: ["juegos", "niños", "infantil", "tobogan"]
       - Query: "Para celiacos" -> keywords: ["celiaco"], synonyms: ["sin tacc", "gluten", "intolerante"]
       - Query: "Con terraza" -> keywords: ["terraza"], synonyms: ["afuera", "aire libre", "patio", "vereda"]
    
    Responde SOLO JSON: {{"tipo": "PRODUCTO" | "VIBE" | "BLOCK", "keywords": ["k1"], "synonyms": ["s1"]}}
    """
    try:
        res = await llm.ainvoke(template)
        clean = res.content.strip().replace("```json", "").replace("```", "")
        data = json.loads(clean)
        
        # Si entró por bypass, forzamos VIBE aunque el LLM diga BLOCK
        if is_safe_bypass and data.get("tipo") == "BLOCK":
            data["tipo"] = "VIBE"

        if 'synonyms' not in data: data['synonyms'] = []
        
        cache.set_json("analysis", cache_key, data)
        return data
    except: 
        # Fallback básico
        return {"tipo": "VIBE", "keywords": [q_lower], "synonyms": []}

def detectar_mencion_exacta(query, df):
    """
    Detecta si el usuario nombró un lugar exacto.
    FIX v7.5: Agregados 'helado', 'heladeria' a la blacklist para evitar matches falsos.
    FIX v7.4: Devuelve el NÚCLEO para permitir menús de opciones.
    """
    if df is None or df.empty: return None
    q_norm = query.lower().strip()
    
    venue_prefixes = {
        "restaurante", "parrilla", "bar", "confiteria", "pizzeria", "bodegon", 
        "cerveceria", "hamburgueseria", "heladeria", "cafe", "bistro", "resto", 
        "rotiseria", "panaderia", "sushi", "casa", "local", "negocio"
    }
    
    stopwords = {"el", "la", "los", "las", "de", "del", "lo", "al", "y", "en", "que", "qué", "tal", "como", "es", "onda", "son"}
    
    # AQUI ESTÁ LA CLAVE: Agregamos helado/heladeria/postres para que no matcheen nombres parciales
    generic_blocklist = {
        "sushi", "pizza", "pizzas", "burger", "hamburguesa", "hamburguesas", 
        "helado", "helados", "heladeria", "heladerias", "crema", "cremas", 
        "birra", "cerveza", "cervezas", "birra", "birras", "cafe", "café", "parrilla", "pasta", 
        "pastas", "milanesa", "milanesas", "ensalada", "comida", "postre", 
        "postres", "resto", "bar", "almuerzo", "cena", "menu"
    }

    nombres = df['restaurante'].unique().tolist()
    nombres.sort(key=len, reverse=True)

    for nombre_real in nombres:
        nombre_lower = nombre_real.lower().strip()
        
        # 1. Match Exacto Total (Solo si es idéntico)
        if nombre_lower == q_norm: 
            return nombre_real

        # Análisis de partes
        parts = nombre_lower.split()
        core_parts = [p for p in parts if re.sub(r'[^\w]', '', p) not in venue_prefixes]
        if not core_parts: continue 
        core_name = " ".join(core_parts) 
        
        # 2. Match de Núcleo
        if len(core_name) > 3:
            pattern = r'(?<!\w)' + re.escape(core_name) + r'(?!\w)'
            if re.search(pattern, q_norm):
                # Si el núcleo detectado está en la lista negra (ej: "Helados"), LO IGNORAMOS.
                if core_name in generic_blocklist:
                    continue
                return core_name.title() 

        # 3. Match Palabra Distintiva
        distinctive_parts = [p for p in core_parts if p not in stopwords]
        if distinctive_parts:
            for part in distinctive_parts:
                clean_part = re.sub(r'[^\w]', '', part)
                if len(clean_part) <= 3: continue
                if clean_part in generic_blocklist: continue
                
                pattern_dist = r'(?<!\w)' + re.escape(clean_part) + r'(?!\w)'
                if re.search(pattern_dist, q_norm):
                    return clean_part.title()
                    
    return None

# Agregá "last_entity=None" en los argumentos
async def clasificar_intencion(query, llm, last_entity=None): 
    """
    Router Inteligente V2 (Corregido para aceptar last_entity)
    """
    
    system_prompt = """
    Eres el cerebro clasificador de un asistente gastronómico.
    Analiza la frase del usuario y clasifícala en UNA de estas 5 categorías.
    
    PRIORIDAD 1: BLOCK (Seguridad y Ofensas)
    - Si la frase contiene INSULTOS, agresiones, palabras obscenas o falta de respeto directa.
    - Ejemplos: "pelotudo", "bobo", "andate a cagar", "hijo de p*", "chupala", "idiota".
    
    PRIORIDAD 2: STATS (Estadísticas)
    - Preguntas explícitas sobre CANTIDADES o CONTEOS.
    - Clave: Empieza con "Cuantos", "Total de", "Numero de", "Que cantidad".
    
    PRIORIDAD 3: SPECIFIC (Info puntual)
    - Preguntas sobre un lugar específico por su nombre.
    - Ejemplos: "Que opinas de Saluzzo?", "Que tal es Growler?", "Que sabes de Panino"?, "¿Donde queda Rancho Grande?", "Horario de Atila".
    
    PRIORIDAD 4: RECOMMENDATION (Búsqueda)
    - Busca opciones para comer o lugares con características.
    - Ejemplos: "mejores cervecerías", "mejores helados", "Lugares con pelotero", "Quiero sushi", "Parrilla barata".
    
    PRIORIDAD 5: GENERAL (Charla y Otros)
    - Saludos, agradecimientos, incoherencias o temas off-topic.
    
    Responde SOLO la palabra de la categoría (ej: BLOCK).
    """
    
    try:
        # Usamos last_entity en el prompt si existe, ayuda al contexto
        context_str = f" (Contexto previo: Hablábamos de {last_entity})" if last_entity else ""
        
        res = await llm.ainvoke(system_prompt + f"\nQUERY USUARIO: '{query}'{context_str}")
        intent = res.content.strip().upper().replace('"', '').replace('.', '')
        
        validos = ["BLOCK", "STATS", "SPECIFIC", "RECOMMENDATION", "GENERAL"]
        
        for v in validos:
            if v in intent: return v
            
        return "GENERAL"
        
    except Exception as e:
        logger.error(f"Error Router: {e}")
        return "GENERAL"
    
async def consultar_estadisticas(query, df, llm):
    """
    Fusión: Robustez técnica (ASCII) + Respuesta Inteligente (Listado).
    """
    try:
        # 1. Extracción inteligente (Mejor que split simple)
        # Le pedimos al LLM que normalice a singular y quite basura
        prompt_extract = f"""
        Analiza la query: "{query}"
        Identifica la CATEGORÍA o PRODUCTO que el usuario quiere contar.
        Ejemplos: 
        - "Cuantas pizzerias hay" -> "pizzeria"
        - "Lugares de sushi" -> "sushi"
        - "Total de bares" -> "bar"
        
        Responde SOLO la palabra clave en singular y minúscula. Si pregunta por todo, responde "total".
        """
        keyword_raw = await llm.ainvoke(prompt_extract)
        keyword = str(keyword_raw.content).strip().lower().replace('"', '').replace('.', '')
        
        # 2. Caso "Total"
        if keyword in ["total", "todo", "restaurantes", "lugares"]:
            total = df['restaurante'].nunique()
            return f"Tengo registrados un total de **{total}** locales en la base de datos.", []

        # 3. Normalización ASCII (Tu lógica ganadora)
        # Quitamos acentos de la keyword para matchear con tus columnas ASCII
        keyword_ascii = unicodedata.normalize('NFD', keyword)
        keyword_ascii = ''.join(ch for ch in keyword_ascii if unicodedata.category(ch) != 'Mn')
        keyword_ascii = re.sub(r'[^\w\s]', '', keyword_ascii)
        
        # 4. Filtrado (Usando tus columnas optimizadas)
        # Asumimos que df ya tiene 'restaurante_ascii' y 'texto_ascii'
        mask = (df['restaurante_ascii'].str.contains(keyword_ascii, case=False, na=False) | 
                df['texto_ascii'].str.contains(keyword_ascii, case=False, na=False))
        
        matches = df[mask]['restaurante'].unique().tolist()
        total_count = len(matches)

        # 5. Generación de Respuesta "Humana" (Mi lógica ganadora)
        if total_count == 0:
            return f"No encontré lugares registrados bajo la categoría **'{keyword}'**.", []
        
        elif total_count == 1:
            return f"Encontré solo uno: **{matches[0]}**.", matches
            
        elif total_count <= 10:
            # Si son pocos, los nombramos todos
            lista_str = ", ".join(matches)
            return f"Encontré **{total_count}** lugares de {keyword}: {lista_str}.", matches
            
        else:
            # Si son muchos, damos el total y ejemplos
            ejemplos = ", ".join(matches[:5])
            return f"¡Un montón! Encontré **{total_count}** lugares de {keyword}. Algunos son: {ejemplos}, entre otros.", matches

    except Exception as e:
        logger.error(f"Error consultar_estadisticas: {e}")
        return "Tuve un problema calculando esa estadística.", []

async def resumir_opiniones_local(query_str, df, llm, topic=None, tone='cordial', es_seleccion_directa=False):
    # 1. Validación básica
    if not query_str: return "Nombre vacío.", None, "", None
    q_clean = query_str.lower().strip()
    encontrados = []
    
    # 2. Búsqueda de coincidencias
    mask_exact = df['restaurante'].str.lower() == q_clean
    if mask_exact.any():
        encontrados = [df[mask_exact].iloc[0]['restaurante']]
    else:
        if es_seleccion_directa:
             if mask_exact.any(): encontrados = [df[mask_exact].iloc[0]['restaurante']]
        else:
            mask = df['restaurante'].str.lower().str.contains(q_clean, na=False, regex=False)
            candidatos = df[mask]['restaurante'].unique().tolist()
            
            if len(candidatos) == 1: 
                encontrados = candidatos
            elif len(candidatos) > 1:
                encontrados = candidatos
                encontrados.sort()
                # Menú de opciones
                labels = []
                for r in encontrados:
                    mask_r = df['restaurante'] == r
                    rowr = df[mask_r].iloc[0]
                    ubi = safe_str(rowr.get('direccion')) or safe_str(rowr.get('zona')) or "Ubicación desconocida"
                    labels.append(f"**{r}** ({ubi})")
                lista_txt = "\n".join([f"{i+1}. {lbl}" for i, lbl in enumerate(labels)])
                return f"Encontré varios lugares con ese nombre. ¿Cuál decís?\n\n{lista_txt}\n\n*(Escribí el número)*", None, "", {"options": encontrados}

    # 3. Si no encontró nada
    if not encontrados: 
        return f"No tengo info de **{query_str}**. Probá con otro nombre.", None, "", None
    
    # 4. Chequeo de Caché
    restaurante = encontrados[0]
    cache_key = f"{restaurante}_{topic}_{sanitize_tone(tone)}" if topic else f"{restaurante}__{sanitize_tone(tone)}"
    cached_text = cache.get_json("resumen_texto", cache_key)
    if cached_text: 
        return f"Acá te paso la data de **{restaurante}**:", restaurante, cached_text, None

    # 5. Preparación de Datos para el Prompt
    # Obtenemos la fila de datos para sacar la ubicación real
    row_data = df[df['restaurante'] == restaurante].iloc[0]
    
    zona_str = safe_str(row_data.get('zona')).lower()
    dir_str = safe_str(row_data.get('direccion')).lower()
    
    # Lógica para determinar la ciudad correcta
    ubicacion_real = "Neuquén Capital" # Default
    if "cipolletti" in zona_str or "cipolletti" in dir_str:
        ubicacion_real = "Cipolletti, Río Negro"
    elif "plottier" in zona_str or "plottier" in dir_str:
        ubicacion_real = "Plottier, Neuquén"
    elif "centenario" in zona_str or "centenario" in dir_str:
        ubicacion_real = "Centenario, Neuquén"
    elif safe_str(row_data.get('zona')): # Si tiene zona pero no es otra ciudad (ej: "Alta Barda")
        ubicacion_real = f"Neuquén Capital (Zona {safe_str(row_data.get('zona'))})"

    sorted_reviews = rankear_reviews_por_topico(df[df['restaurante'] == restaurante], topic)
    reviews_txt = "\n".join([safe_str(r.get('texto'))[:200] for _, r in sorted_reviews.head(10).iterrows()])
    
    contexto_tema = f"El usuario pregunta específicamente sobre: '{topic}'. Resalta eso." if topic else ""
    tone_prefix = tone_system_instruction(tone)
    
    # 6. Prompt Dinámico con ubicación real
    tpl = f"""{tone_prefix}\n
    Analiza el restaurante: {{rest}}.
    UBICACIÓN REAL: {ubicacion_real}.
    Rating: {{rat}}. 
    {contexto_tema}
    
    Reviews de usuarios: {{revs}}
    
    Generá un resumen en Markdown con acento Argentino.
    REGLA: NO digas que está en Buenos Aires. Respetá la ubicación: {ubicacion_real}.
    
    Estructura:
    ## 📊 La onda
    ## 👍 Lo bueno
    ## 💡 A mejorar
    ## 🎯 Veredicto"""
    
    try:
        res = await (ChatPromptTemplate.from_template(tpl) | llm | StrOutputParser()).ainvoke({
            "rest": restaurante, 
            "rat": safe_float(row_data.get('rating_gral')),
            "revs": reviews_txt
        })
    except: res = "No pude generar el resumen."
    
    # 7. Guardado y Retorno final
    cache.set_json("resumen_texto", cache_key, res)
    return f"Acá la data de **{restaurante}**:", restaurante, res, None

async def verificar_candidatos_con_llm(candidatos, df, query, llm):
    """
    JUEZ SEMÁNTICO (V2 - CON BÚSQUEDA DE EVIDENCIA):
    En lugar de leer las primeras 5 reseñas al azar, busca específicamente
    las reseñas que contienen las palabras clave de la query.
    """
    if not candidatos: return []
    
    # 1. Extraemos keywords relevantes de la query (ej: "pelotero")
    stop_short = ["que", "los", "las", "con", "para", "donde", "hay", "lugar"]
    words = query.lower().split()
    keywords = [w for w in words if len(w) > 3 and w not in stop_short]
    
    texto_validacion = ""
    
    # Analizamos Top 10
    for local in candidatos[:10]:
        mask = df['restaurante'] == local
        if not mask.any(): continue
        
        all_reviews = df[mask]['texto'].fillna("").astype(str)
        
        # 2. BÚSQUEDA DE EVIDENCIA
        # Buscamos reseñas que contengan ALGUNA de las keywords
        evidence_reviews = []
        found_evidence = False
        
        if keywords:
            for k in keywords:
                # Buscamos filas que contengan la keyword 'k'
                matches = all_reviews[all_reviews.str.lower().str.contains(k, regex=False)]
                if not matches.empty:
                    # Tomamos hasta 2 reseñas que hablen del tema
                    evidence_reviews.extend(matches.head(2).tolist())
                    found_evidence = True
        
        # 3. ARMADO DEL CONTEXTO
        if found_evidence:
            # Si encontramos evidencia, se la mostramos al Juez
            snippet = " ... ".join(evidence_reviews)[:900]
            prefix = "EVIDENCIA ENCONTRADA:"
        else:
            # Si es búsqueda semántica pura (sin keyword exacta), mandamos las generales
            snippet = " ... ".join(all_reviews.head(5).tolist())[:900]
            prefix = "RESEÑAS GENERALES:"
            
        texto_validacion += f"- LOCAL: {local}\n  {prefix} \"...{snippet}...\"\n\n"

    prompt = f"""
    Eres un JUEZ DE CALIDAD. Query usuario: "{query}"
    
    Analiza la EVIDENCIA de los locales y decide cuáles aprobar.
    
    REGLAS:
    1. APROBAR si la evidencia confirma que tiene lo que se pide (ej: "Lindo pelotero").
    2. ELIMINAR si la mención es NEGATIVA (ej: "No tiene pelotero", "Sacaron el pelotero").
    3. ELIMINAR si el contexto es irónico (ej: "Parece un pelotero de tanto ruido").
    4. Si la evidencia es vaga pero positiva, APROBAR.
    
    CANDIDATOS:
    {texto_validacion}
    
    Responde SOLO un JSON: ["Local A", "Local B"]
    """
    
    try:
        res = await llm.ainvoke(prompt)
        clean = res.content.strip().replace("```json", "").replace("```", "")
        validos = json.loads(clean)
        return validos
    except Exception as e:
        logger.error(f"Error Juez LLM: {e}")
        return candidatos

# ==========================================
# 6. ROUTER PRINCIPAL
# ==========================================
async def procesar_consulta(query, df, vectorstore, llm_mini, llm_smart, ctx=None, user_ip=None):
    if ctx is None: ctx = {}
    tone = sanitize_tone(ctx.get('tone'))
    
    # ==========================================
    # 1. CAPA DE SEGURIDAD (NUCLEAR)
    # ==========================================
    if user_ip and cache.get_value(f"ban:{user_ip}"): 
        return "⛔ Sistema bloqueado.", "blocked", None, [], [], ""
    
    strikes = ctx.get('strikes', 0)
    if strikes >= 5: return "⛔ Bloqueado.", "blocked", None, [], [], ""

    if check_keyword_ban(query):
        ctx['strikes'] = strikes + 1
        return f"Epa, esa búsqueda no va. ({strikes+1}/5)", "rag", None, [], [], ""

    # ==========================================
    # 2. CONTEXTO NUMÉRICO (MENÚS)
    # ==========================================
    if 'pending_options' in ctx and query.strip().isdigit():
        num = int(query.strip())
        pending = ctx['pending_options']
        opciones = pending.get('options', [])
        if 1 <= num <= len(opciones):
            seleccion = opciones[num - 1]
            ctx['last_entity'] = seleccion
            original_topic = ctx.get('original_query', seleccion)
            resp, nombre_real, det, _ = await resumir_opiniones_local(seleccion, df, llm_mini, original_topic, tone, True)
            cards = await obtener_restaurant_cards([nombre_real], df, llm_mini, original_topic, tone)
            locs = obtener_coordenadas([nombre_real], df)
            return resp, "resumen", None, locs, cards, det
        return f"Elegí entre 1 y {len(opciones)}", "resumen", pending, [], [], ""

    # ==========================================
    # 3. SMART ROUTING (EL CEREBRO V8)
    # ==========================================
    last_ent = ctx.get('last_entity')
# --- CORRECCIÓN INICIO ---
    # El router ahora devuelve un STRING directo ("STATS", "SPECIFIC", etc.)
    intent_raw = await clasificar_intencion(query, llm_smart, last_entity=last_ent)
    
    intent = intent_raw
    entity_detected = None # La versión actual de tu router no extrae entidades, solo intención.

    # Ajuste de compatibilidad: Tu router devuelve "SPECIFIC", pero tu lógica abajo busca "SPECIFIC_INFO"
    if intent == "SPECIFIC":
        intent = "SPECIFIC_INFO"
    # --- CORRECCIÓN FIN ---

# --- CAMINO A: ESTADÍSTICAS ---
    if intent == "STATS":
        
        # Si pregunto estadísticas, rompo la charla sobre un lugar específico
        if 'last_entity' in ctx: del ctx['last_entity'] # <--- LIMPIEZA
        
        resp, locales = await consultar_estadisticas(query, df, llm_mini)
        cards = obtener_restaurant_cards_simple(locales, df)
        locs = obtener_coordenadas(locales, df)
        return resp, "estadisticas", None, locs, cards, ""
        

    # --- CAMINO B: INFO ESPECÍFICA (UN LUGAR) ---
    if intent == "SPECIFIC_INFO":
        target = None
        if entity_detected == "LAST_ENTITY": target = last_ent
        elif entity_detected: target = entity_detected
        else: target = query # Fallback

        if target:
            # Intentamos resolver el nombre real
            # Usamos una búsqueda simple primero para validar existencia
            match_exists = False
            nombre_candidato = detectar_mencion_exacta(target, df) # Usamos regex helper
            if nombre_candidato:
                target = nombre_candidato
                match_exists = True
            else:
                # Busqueda laxa si regex falló
                mask = df['restaurante'].str.lower().str.contains(target.lower().strip(), na=False, regex=False)
                if mask.any(): match_exists = True

            if match_exists:
                ctx['original_query'] = query
                resp, nombre_final, det, opciones = await resumir_opiniones_local(target, df, llm_mini, query, tone)
                
                if opciones: return resp, "resumen", opciones, [], [], ""
                
                if nombre_final:
                    ctx['last_entity'] = nombre_final
                    cards = await obtener_restaurant_cards([nombre_final], df, llm_mini, query, tone)
                    locs = obtener_coordenadas([nombre_final], df)
                    return resp, "resumen", None, locs, cards, det
            
            # Si era específico pero no existe, avisamos (salvo que sea recomendación disfrazada)
            if entity_detected and entity_detected != "LAST_ENTITY":
                 # Fallback inteligente: si no encontramos "Mc Donalds", capaz Pinecone encuentra algo similar
                 pass 
        
        # Si falló lo específico, pasamos a Recomendación
        intent = "RECOMMENDATION"

# --- CAMINO C: RECOMENDACIÓN (PIPELINE ESTRICTO) ---
    if intent == "RECOMMENDATION":
        # Si busco recomendaciones nuevas, olvido el lugar anterior
        if 'last_entity' in ctx: del ctx['last_entity'] # <--- LIMPIEZA
        try:
            # 1. Análisis Semántico
            analisis = await analizar_query_semantica(query, llm_smart)
            if analisis.get("tipo") == "BLOCK":
                ctx['strikes'] = strikes + 1
                return f"Epa, esa búsqueda no va. ({strikes+1}/5)", "rag", None, [], [], ""

            keywords = analisis.get("keywords", [])
            synonyms = analisis.get("synonyms", [])
            
            # Preparamos los términos de filtrado estricto
            filtro_terms = set(keywords)
            if synonyms: filtro_terms.update(synonyms)
            filtro_terms = [t.lower() for t in filtro_terms if len(t) > 3]

            # 2. Búsqueda Vectorial (Pinecone) - El "Barrido Amplio"
            docs = vectorstore.similarity_search(query, k=60) # Traemos bastantes para filtrar
            seen = set()
            candidatos_crudos = []
            for d in docs:
                nom = d.metadata.get('nombre')
                if nom and nom not in seen:
                    seen.add(nom)
                    candidatos_crudos.append(nom)

            # 3. FILTRADO POR EVIDENCIA (Hard Filter)
            grupo_alta_relevancia = []
            grupo_baja_relevancia = []

            if filtro_terms:
                # Preparamos el patrón Regex una sola vez (ej: "pelotero|juegos|niños")
                import re
                patron_regex = '|'.join([re.escape(t) for t in filtro_terms])
                
                for local in candidatos_crudos:
                    mask = df['restaurante'] == local
                    if not mask.any(): continue
                    
                    # === CAMBIO CLAVE ===
                    # No usamos head(30). Buscamos en TODA la columna de texto de este local.
                    # str.contains es vectorizado y ultra rápido, incluso con 2000 reseñas.
                    
                    # Obtenemos la serie de textos de este restaurante
                    series_textos = df[mask]['texto'].fillna("").astype(str).str.lower()
                    
                    # Verificamos si ALGUNA fila contiene CUALQUIERA de los términos
                    tiene_match = series_textos.str.contains(patron_regex, regex=True).any()
                    
                    if tiene_match:
                        grupo_alta_relevancia.append(local)
                    else:
                        grupo_baja_relevancia.append(local)
            else:
                grupo_alta_relevancia = candidatos_crudos

            # 4. SELECCIÓN PARA EL JUEZ
            # Si encontramos lugares con la palabra clave, SOLO procesamos esos.
            # Ignoramos el resto (aunque tengan 5 estrellas, si no dicen "pelotero", no sirven).
            
            candidatos_a_verificar = []
            
            if grupo_alta_relevancia:
                # Si hay muchos con la palabra, ahí sí desempatamos por calidad para no saturar al LLM
                def sort_by_quality(nombre):
                    mask = df['restaurante'] == nombre
                    row = df[mask].iloc[0]
                    return safe_float(row.get('rating_gral'))
                
                grupo_alta_relevancia.sort(key=sort_by_quality, reverse=True)
                # Tomamos el Top 10 de los que TIENEN la palabra
                candidatos_a_verificar = grupo_alta_relevancia[:10]
            else:
                # Fallback: Si nadie dice la palabra, usamos los mejores del vector search
                candidatos_a_verificar = grupo_baja_relevancia[:10]

            # 5. EL JUEZ LLM (Verificación de Contexto)
            # Ahora el juez recibe una lista de lugares que YA sabemos que contienen la palabra.
            # Su único trabajo es filtrar los "No tiene..."
            locales_verificados = await verificar_candidatos_con_llm(
                candidatos_a_verificar, df, query, llm_mini
            )
            
            # Si el Juez mata a todos (muy estricto), fallback a los candidatos con palabra clave
            if not locales_verificados and grupo_alta_relevancia:
                locales_verificados = candidatos_a_verificar[:3]

            # 6. RANKING FINAL (Ahora sí, por calidad)
            # De los que pasaron TODAS las pruebas, mostramos los mejores.
            def calcular_score_final(nombre_local):
                mask = df['restaurante'] == nombre_local
                if not mask.any(): return 0
                row = df[mask].iloc[0]
                rat = safe_float(row.get('rating_gral'))
                revs = safe_int(row.get('total_reviews_google'))
                return rat + (math.log10(revs + 1) * 0.2) # Logaritmo suave

            locales_verificados.sort(key=calcular_score_final, reverse=True)
            locales_finales = locales_verificados[:5]

            if not locales_finales: 
                return "No encontré lugares que cumplan con ese requisito específico.", "rag", None, [], [], ""

            # 7. Generación de Cards y Respuesta
            cards = await obtener_restaurant_cards(
                locales_finales, df, llm_mini, query, tone, 
                strict_mode=False, 
                keywords_list=keywords,
                synonyms_list=synonyms
            )
            nombres_finales = [c.nombre for c in cards]
            locs = obtener_coordenadas(nombres_finales, df)
            
            detalles_lugares = "\n".join([f"- {c.nombre}: {c.descripcion}" for c in cards])
            prefix = tone_system_instruction(tone)
            
            prompt_rag = (
                f"{prefix}\n"
                f"SITUACIÓN: El usuario buscó '{query}'.\n"
                f"Resultados VERIFICADOS:\n{detalles_lugares}\n\n"
                f"INSTRUCCIONES:\n"
                "1. Basate EXCLUSIVAMENTE en las descripciones provistas.\n"
                "2. Confirmale al usuario que estos lugares cumplen con su búsqueda.\n"
                "3. Genera una recomendación útil."
            )
            
            rag_resp = await llm_mini.ainvoke(prompt_rag)
            return rag_resp.content, "rag", None, locs, cards, ""
            
        except Exception as e:
            logger.error(f"Error RAG: {e}")
            return "Tuve un problema técnico buscando eso.", "rag", None, [], [], ""
        
       
    # 1. Manejo de Bloqueo por LLM
    if intent == "BLOCK":
        ctx['strikes'] = strikes + 1
        return "Epa, bajemos un cambio. Mantené el respeto, estoy acá para ayudar. (Strike sumado)", "general", None, [], [], ""
    
    if intent == "GENERAL":
        try:
            # 1. Usamos la personalidad que ya definiste
            prefix = tone_system_instruction(tone)
            
            # 2. Prompt simple: "Sé educado pero volvé a la comida"
            prompt_chat = (
                f"{prefix}\n"
                f"SITUACIÓN: El usuario dijo: '{query}'.\n"
                f"INSTRUCCIONES:\n"
                "1. Responde de forma natural y breve (máximo 2 oraciones).\n"
                "2. Si es un saludo, devolvé el saludo con onda.\n"
                "3. Si es un agradecimiento, decí 'de nada'.\n"
                "4. IMPORTANTE: SIEMPRE terminá invitando a buscar comida (ej: '¿Buscamos algo para cenar?', '¿Tenés hambre?').\n"
                "5. No inventes lugares ni datos, es solo charla."
            )
            
            resp_chat = await llm_mini.ainvoke(prompt_chat)
            
            # Retornamos tipo "general" para que el frontend no dibuje mapas ni cards
            return resp_chat.content, "general", None, [], [], ""
            
        except Exception as e:
            logger.error(f"Error General: {e}")
            return "¡Buenas! ¿En qué te puedo ayudar para comer hoy?", "general", None, [], [], ""
        
# ==========================================
# 7. ENDPOINTS
# ==========================================

@app.get("/")
def read_root(): return {"status": "online", "message": "API OK v6.8"}

@app.get("/health")
def health_check(): return {"status": "healthy", "df_size": len(df) if df is not None else 0}

@app.get("/restaurant/{nombre}", response_model=RestaurantDetail)
async def get_restaurant_detail(nombre: str, topic: Optional[str] = None, tone: Optional[str] = None):
    global df, llm_mini
    if not nombre: raise HTTPException(status_code=404)
    mask = df['restaurante'].str.lower() == nombre.lower()
    if not mask.any(): raise HTTPException(status_code=404, detail="No encontrado")
    
    rest_df = df[mask]
    row = rest_df.iloc[0]
    nombre_real = safe_str(row['restaurante'])
    
    sorted_reviews = rankear_reviews_por_topico(rest_df, topic)
    reviews_list = []
    for _, r in sorted_reviews.head(8).iterrows():
        if len(safe_str(r.get('texto'))) > 10:
            reviews_list.append(ReviewDetail(
                autor=formatear_autor(r.get('autor')),
                rating=safe_int(r.get('rating_user')),
                texto=safe_str(r.get('texto'))[:2000],
                fecha=safe_str(r.get('fecha'))
            ))

    tone = sanitize_tone(tone)
    cache_key = f"{nombre_real}_{topic}_{tone}" if topic else f"{nombre_real}__{tone}"
    analisis = cache.get_json("detail_topic", cache_key)
    if not analisis:
        sample = " | ".join([r.texto[:150] for r in reviews_list[:5]])
        contexto_tema = f"IMPORTANTE: El usuario busca '{topic}'. Resalta qué dicen las reseñas sobre eso." if topic else ""
        prefix = tone_system_instruction(tone)
        prompt_txt = f"""{prefix}\nAnaliza "{nombre_real}". {contexto_tema}
        Responde SOLO JSON válido:
        {{"resumen": "descripción de 2 oraciones...", "positivos": ["p1", "p2"], "negativos": ["n1"]}}
        Reviews: {sample}"""
        try:
            res = await llm_mini.ainvoke(prompt_txt)
            clean = res.content.strip().replace("```json","").replace("```","")
            analisis = json.loads(clean)
            cache.set_json("detail_topic", cache_key, analisis)
        except: 
            analisis = {"resumen": "Info no disponible momentáneamente.", "positivos": [], "negativos": []}

    return RestaurantDetail(
        nombre=nombre_real,
        rating=safe_float(row.get('rating_gral')),
        total_reviews=safe_int(row.get('total_reviews_google')),
        direccion=safe_str(row.get('direccion')),
        barrio=safe_str(row.get('barrio')),
        zona=safe_str(row.get('zona')),
        lat=safe_float(row.get('latitud')),
        lng=safe_float(row.get('longitud')),
        resumen_general=safe_str(analisis.get("resumen")),
        aspectos_positivos=analisis.get("positivos", []),
        aspectos_negativos=analisis.get("negativos", []),
        reviews=reviews_list
    )

@app.post("/chat", response_model=QueryResponse)
async def chat(req: QueryRequest, request: Request):
    try:
        # LOG DE ENTRADA: ¿Qué contexto me manda el frontend?
        logger.info(f"📥 Contexto Recibido: {req.conversation_context}")
        ctx = req.conversation_context.copy() if req.conversation_context else {}
        if req.tone: ctx['tone'] = sanitize_tone(req.tone)
        
        client_ip = request.client.host
        
        resp, mode, pend, locs, cards, det = await procesar_consulta(
            req.query, df, vectorstore, llm_mini, llm_smart, ctx, user_ip=client_ip
        )
        
        new_ctx = ctx.copy() if ctx else {}
        if pend: new_ctx['pending_options'] = pend
        elif 'pending_options' in new_ctx: del new_ctx['pending_options']
        
        # if req.conversation_context and 'last_entity' in req.conversation_context and 'last_entity' not in new_ctx:
        #      new_ctx['last_entity'] = req.conversation_context['last_entity']
        if 'original_query' in req.conversation_context and 'original_query' not in new_ctx:
             new_ctx['original_query'] = req.conversation_context['original_query']

        # LOG DE SALIDA: ¿Qué contexto le devuelvo?
        logger.info(f"📤 Contexto Saliente: {new_ctx}")

        return QueryResponse(
            response=resp, mode=mode, conversation_context=new_ctx,
            locations=locs, restaurant_cards=cards, detail_content=det
        )
    except Exception as e:
        logger.error(f"Error Chat: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)