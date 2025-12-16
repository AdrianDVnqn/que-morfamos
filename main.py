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
    base = "Sos un asistente gastronómico experto en Neuquén. Hablas con acento argentino/porteño."
    if tone == 'cordial': return f'{base} Sos cordial, educado y respetuoso.'
    if tone == 'soberbio': return f'{base} Usas un tono soberbio, pedante y "cheto".'
    if tone == 'sassy': return f'{base} Sos irónico, picante y tenés un humor mordaz.'
    return f'{base} Sos cordial y claro.'

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

async def obtener_restaurant_cards(nombres_restaurantes, df, llm, query_context=None, tone='cordial', strict_mode=True, keywords_list=None):
    cards = []
    tasks = [] 
    search_terms = keywords_list if keywords_list else ([query_context] if query_context else [])

    for nombre in nombres_restaurantes:
        if not nombre: continue
        mask = df['restaurante'].str.lower() == nombre.lower()
        if mask.any():
            rest_df = df[mask]
            row = rest_df.iloc[0]
            nombre_real = safe_str(row['restaurante'])

            frase = ""
            autor = ""
            primary_topic = search_terms[0] if search_terms else None
            best_review = seleccionar_mejor_review(rest_df, primary_topic)
            
            if query_context:
                if strict_mode:
                    if best_review is None: continue
                else:
                    if best_review is None: best_review = seleccionar_mejor_review(rest_df, None)

            if best_review is not None:
                frase = safe_str(best_review['texto'])[:350] + "..."
                autor = formatear_autor(best_review.get('autor'))

            cache_key = f"{nombre_real}__{sanitize_tone(tone)}"
            desc = cache.get_json("desc", cache_key)
            if desc:
                tasks.append({"type": "cached", "val": desc, "row": row, "frase": frase, "autor": autor, "nombre_real": nombre_real})
            else:
                sample = " ".join([safe_str(t) for t in rest_df['texto'].head(5)])[:800]
                task_coro = generar_descripcion_async(llm, nombre_real, sample, tone)
                tasks.append({"type": "generate", "val": task_coro, "row": row, "frase": frase, "autor": autor, "nombre_real": nombre_real})

    generations_needed = [t['val'] for t in tasks if t['type'] == 'generate']
    if generations_needed:
        results = await asyncio.gather(*generations_needed)
    
    gen_idx = 0
    for item in tasks:
        descripcion = ""
        if item['type'] == 'cached':
            descripcion = item['val']
        else:
            descripcion = results[gen_idx]
            gen_idx += 1
            cache.set_json("desc", f"{item['nombre_real']}__{sanitize_tone(tone)}", descripcion)

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
    
    if keywords_list and len(cards) > 1:
        def calcular_score_densidad(card):
            mask_rest = df['restaurante'] == card.nombre
            texto_gigante = " ".join(df[mask_rest]['texto'].fillna("").astype(str).str.lower())
            hits = 0
            for k in keywords_list: hits += texto_gigante.count(k)
            return hits / (card.total_reviews + 50)
        cards.sort(key=calcular_score_densidad, reverse=True)

    return cards

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
    """ USA LLM_SMART. Retorna: {tipo, keywords} """
    # 0. BYPASS MANUAL DE SEGURIDAD (White-list absoluta para términos problemáticos pero seguros)
    # Si la query habla de helados, ES SEGURA y es VIBE. Cortamos acá.
    q_lower = query.lower()
    if "helad" in q_lower: 
        return {"tipo": "VIBE", "keywords": ["heladeria"]}

    cache_key = f"analysis_{q_lower.strip()}"
    cached = cache.get_json("analysis", cache_key)
    if cached: return cached

    template = """
    Analiza la intención del usuario. Query: "{query}"
    1. SEGURIDAD:
       - Si contiene términos sexuales, insultos o violencia ("tetas", "pito", "travesti", "culo", "puta"), MARCA "tipo": "BLOCK".
       - EXCEPCIONES (NO BLOQUEAR): "Crema", "Leche", "Pechuga", "Chorizo", "Huevos", "Salchicha".
    2. CLASIFICACIÓN (Si es seguro):
       - "PRODUCTO": Plato o ingrediente concreto (Pizza, Sushi, Flan).
       - "VIBE": Tipo de local (Cerveceria, Parrilla), ocasión o Bebida.
    3. KEYWORDS: Extrae sustantivos en SINGULAR. Ignora "mejores", "top".
    Responde SOLO JSON: {{"tipo": "PRODUCTO" | "VIBE" | "BLOCK", "keywords": ["k1"]}}
    """
    try:
        res = await llm.ainvoke(template)
        clean = res.content.strip().replace("```json", "").replace("```", "")
        data = json.loads(clean)
        
        stopwords_ranking = ["mejores", "mejor", "top", "ranking", "los", "las", "el", "la", "de", "en", "neuquen"]
        words = q_lower.split()
        for w in words:
            if w not in stopwords_ranking:
                w_sing = w
                if w.endswith('es') and len(w) > 4: w_sing = w[:-2]
                elif w.endswith('s') and len(w) > 3: w_sing = w[:-1]
                if w_sing not in data.get('keywords', []):
                    if 'keywords' not in data: data['keywords'] = []
                    data['keywords'].append(w_sing)
        cache.set_json("analysis", cache_key, data)
        return data
    except: return {"tipo": "VIBE", "keywords": [q_lower]}

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

async def clasificar_intencion(query, llm, last_entity=None):
    """
    USA LLM_SMART. 
    Define si el usuario quiere info de UN lugar específico o una LISTA de opciones.
    """
    pista_contexto = f"Contexto anterior: '{last_entity}'" if last_entity else "Sin contexto previo."

    template = f"""
    Eres el ROUTER de una IA Gastronómica. Tu trabajo es clasificar la query.
    Query: "{{query}}"
    {pista_contexto}

    REGLAS DE CLASIFICACIÓN:
    1. SPECIFIC_INFO: El usuario pregunta por UN lugar específico por su nombre.
       - Ej: "Que tal es Antares", "Opiniones de La Nonna", "Precio en Atu", "Donde queda Growler".
       - Ej: "y es caro?" (Refiere al contexto anterior).
    
    2. RECOMMENDATION: El usuario busca opciones, categorías o características (Plural o Genérico).
       - Ej: "Lugares para ir con familia", "Donde comer pizza", "Mejores cervecerias", "Algo con pelotero".
       - Ej: "Heladerias", "Cafeterias", "Lugares lindos".
       
    3. STATS: Preguntas de cantidad.
       - Ej: "Cuantos locales tenes", "Total de parrillas".

    Responde JSON: {{"intent": "SPECIFIC_INFO" | "RECOMMENDATION" | "STATS", "entity": "NombreDetectado" | "LAST_ENTITY" | null}}
    """
    try:
        res = await llm.ainvoke(template)
        clean = res.content.strip().replace("```json", "").replace("```", "")
        return json.loads(clean)
    except: 
        # Ante la duda, asumimos recomendación que es más seguro
        return {"intent": "RECOMMENDATION", "entity": None}
async def consultar_estadisticas(query, df, llm):
    """ USA LLM_MINI """
    try:
        prompt = f"Extrae palabra clave para filtrar (ej: 'pizzerias' -> 'pizza'). Query: {query}. Solo la palabra."
        keyword_raw = await llm.ainvoke(prompt)
        keyword_text = str(keyword_raw.content).strip().lower()
        keyword_text = re.sub(r'[^\w\s]', '', keyword_text)
        
        klist = get_keywords_from_topic(keyword_text)
        keyword = klist[0] if klist else (keyword_text.split()[0] if keyword_text else "")
        if not keyword:
            qk = get_keywords_from_topic(query)
            keyword = qk[0] if qk else (query.split()[0] if query else "")
        keyword = keyword.strip()
        
        total = df['restaurante'].nunique()
        if "total" in keyword or len(keyword) < 2:
            return f"Tengo registrados **{total}** restaurantes.", []
            
        keyword_ascii = unicodedata.normalize('NFD', keyword)
        keyword_ascii = ''.join(ch for ch in keyword_ascii if unicodedata.category(ch) != 'Mn')
        keyword_ascii = re.sub(r'[^\w\s]', '', keyword_ascii)
        
        mask = (df['restaurante_ascii'].str.contains(keyword_ascii, na=False) | 
                df['texto_ascii'].str.contains(keyword_ascii, na=False))
        locales_filtrados = df[mask]['restaurante'].unique().tolist()
        
        return f"Encontré **{len(locales_filtrados)}** lugares relacionados con '{keyword}'.", locales_filtrados
    except Exception as e:
        logger.error(f"Error consultar_estadisticas: {e}")
        return "No pude calcular esa estadística.", []

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
    # El LLM decide qué quiere el usuario
    clasificacion = await clasificar_intencion(query, llm_smart, last_entity=last_ent)
    intent = clasificacion.get("intent")
    entity_detected = clasificacion.get("entity")

    # --- CAMINO A: ESTADÍSTICAS ---
    if intent == "STATS":
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

    # --- CAMINO C: RECOMENDACIÓN (RAG + PONDERACIÓN) ---
    if intent == "RECOMMENDATION":
        try:
            # 1. Análisis Semántico
            analisis = await analizar_query_semantica(query, llm_smart)
            if analisis.get("tipo") == "BLOCK":
                ctx['strikes'] = strikes + 1
                return f"Epa, esa búsqueda no va. ({strikes+1}/5)", "rag", None, [], [], ""

            keywords = analisis.get("keywords", [])
            tipo_busqueda = analisis.get("tipo", "VIBE")
            
            # 2. Búsqueda Vectorial (Pinecone)
            docs = vectorstore.similarity_search(query, k=50) # Traemos 50 candidatos
            seen = set()
            locales_preliminares = []
            for d in docs:
                nom = d.metadata.get('nombre')
                if nom and nom not in seen:
                    seen.add(nom)
                    locales_preliminares.append(nom)

            # 3. Filtrado por Keywords
            locales_filtrados = []
            if tipo_busqueda == "PRODUCTO":
                for local in locales_preliminares:
                    mask = df['restaurante'].str.lower() == local.lower()
                    if mask.any():
                        texto_gigante = " ".join(df[mask]['texto'].fillna("").astype(str).str.lower())
                        for k in keywords:
                            if k in texto_gigante:
                                locales_filtrados.append(local)
                                break
            else:
                locales_filtrados = locales_preliminares

            # 4. RANKING PONDERADO (LA FORMULA MÁGICA)
            # -------------------------------------------------------
            def calcular_score_calidad(nombre_local):
                mask = df['restaurante'].str.lower() == nombre_local.lower()
                if not mask.any(): return 0
                row = df[mask].iloc[0]
                
                rat = safe_float(row.get('rating_gral'))
                revs = safe_int(row.get('total_reviews_google'))
                
                # Filtro anti-fantasmas (menos de 10 reviews no rankean)
                if revs < 10: return 0 
                
                # FÓRMULA: Rating + (Log10(Reviews) * 0.3)
                # Esto hace que 4.8 con 1000 reviews le gane a 5.0 con 10 reviews.
                return rat + (math.log10(revs + 1) * 0.3)
            # -------------------------------------------------------

            # Ordenamos y cortamos el Top 5
            locales_filtrados.sort(key=calcular_score_calidad, reverse=True)
            locales_confirmados = locales_filtrados[:5]

            if not locales_confirmados: 
                return "No encontré lugares que coincidan con tu búsqueda.", "rag", None, [], [], ""

            # 5. Generación de Respuesta
            cards = await obtener_restaurant_cards(
                locales_confirmados, df, llm_mini, query, tone, 
                (tipo_busqueda == "PRODUCTO"), keywords
            )
            nombres_finales = [c.nombre for c in cards]
            locs = obtener_coordenadas(nombres_finales, df)
            
            detalles_lugares = "\n".join([f"- {c.nombre}: {c.descripcion}" for c in cards])
            prefix = tone_system_instruction(tone)
            prompt_rag = (
                f"{prefix}\nEl usuario buscó: '{query}'.\n"
                f"Lugares seleccionados (ordenados por calidad/relevancia):\n{detalles_lugares}\n"
                f"INSTRUCCIONES:\n1. Saluda: 'Si estás buscando {query}, te recomiendo...'.\n"
                "2. Si la query es rara, saluda genérico.\n"
                "3. Genera la lista."
            )
            rag_resp = await llm_mini.ainvoke(prompt_rag)
            return rag_resp.content, "rag", None, locs, cards, ""
            
        except Exception as e:
            logger.error(f"Error RAG: {e}")
            return "Tuve un problema técnico buscando eso.", "rag", None, [], [], ""

    return "No entendí, ¿podés reformular?", "rag", None, [], [], ""

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
        ctx = req.conversation_context.copy() if req.conversation_context else {}
        if req.tone: ctx['tone'] = sanitize_tone(req.tone)
        
        client_ip = request.client.host
        
        resp, mode, pend, locs, cards, det = await procesar_consulta(
            req.query, df, vectorstore, llm_mini, llm_smart, ctx, user_ip=client_ip
        )
        
        new_ctx = ctx.copy() if ctx else {}
        if pend: new_ctx['pending_options'] = pend
        elif 'pending_options' in new_ctx: del new_ctx['pending_options']
        
        if req.conversation_context and 'last_entity' in req.conversation_context and 'last_entity' not in new_ctx:
             new_ctx['last_entity'] = req.conversation_context['last_entity']
        if 'original_query' in req.conversation_context and 'original_query' not in new_ctx:
             new_ctx['original_query'] = req.conversation_context['original_query']

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