import os
import json
import re
import unicodedata
import asyncio
import logging
import pandas as pd
import numpy as np
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Query
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

cache = RedisCacheManager(UPSTASH_REDIS_REST_URL, UPSTASH_REDIS_REST_TOKEN)

app = FastAPI(title="Que Morfamos API (Semantic)", version="5.5.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Variables globales
df = None
vectorstore = None
llm = None

# --- MODELOS PYDANTIC ---
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

# ==========================================
# 2. STARTUP
# ==========================================
@app.on_event("startup")
async def startup_event():
    global df, vectorstore, llm
    logger.info("☁️ Iniciando servidor...")
    
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
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
        logger.info("✅ IA lista.")
    except Exception as e:
        logger.error(f"❌ Error IA: {e}")

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

# ===================== TONOS =====================
ALLOWED_TONES = {'cordial', 'soberbio', 'sassy'}
def sanitize_tone(t):
    if not t: return 'cordial'
    tt = str(t).lower().strip()
    return tt if tt in ALLOWED_TONES else 'cordial'

def tone_system_instruction(tone):
    tone = sanitize_tone(tone)
    # Cambio sutil: definimos el ACENTO/ORIGEN del asistente, no su UBICACIÓN actual.
    base = "Sos un asistente gastronómico experto en Neuquén. Hablas con acento argentino/porteño."
    
    if tone == 'cordial':
        return f'{base} Sos cordial, educado y respetuoso.'
    if tone == 'soberbio':
        return f'{base} Usas un tono soberbio, pedante y "cheto".'
    if tone == 'sassy':
        return f'{base} Sos irónico, picante y tenés un humor mordaz.'
    return f'{base} Sos cordial y claro.'

# ==========================================
# 3. LÓGICA DE NEGOCIO
# ==========================================

def get_keywords_from_topic(topic):
    if not topic: return []
    stopwords = {
        "de", "la", "el", "en", "y", "que", "los", "las", "un", "una", "del", "para", "con", 
        "donde", "hay", "lugar", "lugares", "comer", "mejor", "mejores", "neuquen", 
        "opiniones", "info", "sobre", "que", "onda", "tal", "opinas", "tienen", "tiene", "busco",
        "quisiera", "saber", "decime", "conocés", "conoces", "restaurante", "restaurantes"
    }
    words = safe_str(topic).lower().split()
    clean_words = [w for w in words if w not in stopwords and len(w) > 2]
    stemmed_words = []
    for w in clean_words:
        if len(w) > 5:
            stemmed_words.append(w[:-2]) 
        else:
            stemmed_words.append(w)
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

    # Lógica de RAG
    if topic_query:
        top_match = sorted_df.iloc[0]
        if top_match['score_topic'] > 0:
            if len(safe_str(top_match['texto'])) >= 4:
                return top_match
        return None # Si no hay match de topic

    # Lógica de Info General
    candidatas = sorted_df[sorted_df['texto'].str.len() > 25] 
    if not candidatas.empty:
        return candidatas.iloc[0]
    return sorted_df.iloc[0]

async def generar_descripcion_async(llm, nombre, sample, tone='cordial'):
    try:
        prefix = tone_system_instruction(tone)
        prompt = f"{prefix}\nDescribe '{nombre}' en máx 15 palabras atractivas basado en: {sample}"
        res = await llm.ainvoke(prompt)
        return res.content.strip().replace('"','')
    except:
        return "Restaurante popular en Neuquén."

# === ACTUALIZACIÓN: OBTENER CARDS CON STRICT MODE ===
async def obtener_restaurant_cards(nombres_restaurantes, df, llm, query_context=None, tone='cordial', strict_mode=True, keywords_list=None):
    cards = []
    tasks = [] 
    
    for nombre in nombres_restaurantes:
        if not nombre: continue
        mask = df['restaurante'].str.lower() == nombre.lower()
        if mask.any():
            rest_df = df[mask]
            row = rest_df.iloc[0]
            nombre_real = safe_str(row['restaurante'])

            frase = ""
            autor = ""
            
            # Buscamos review relevante
            best_review = seleccionar_mejor_review(rest_df, query_context)
            
            # === PATOVICA DINÁMICO ===
            if query_context and strict_mode:
                # MODO ESTRICTO (Para comidas): Si no hay mención, descartar.
                if best_review is None:
                    continue
            elif query_context and not strict_mode:
                # MODO FLEXIBLE (Para citas/vibes):
                # Si no encontramos la palabra exacta "cita", NO descartamos.
                # Usamos fallback a la mejor review general.
                if best_review is None:
                    best_review = seleccionar_mejor_review(rest_df, None)
            # ==========================

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
        
    # Reordenamiento por densidad (Usamos keywords_list si existe)
    if keywords_list and len(cards) > 1:
        def calcular_score_densidad(card):
            mask_rest = df['restaurante'] == card.nombre
            # Unimos todo el texto para conteo rápido
            texto_gigante = " ".join(df[mask_rest]['texto'].fillna("").astype(str).str.lower())
            hits = 0
            for k in keywords_list: hits += texto_gigante.count(k)
            # Suavizado +10
            return hits / (card.total_reviews + 10)
        
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
# 4. INTENCIÓN Y DETECCIÓN (BRAIN)
# ==========================================

# === NUEVO: ANÁLISIS SEMÁNTICO (PRODUCTO vs VIBE) ===
async def analizar_query_semantica(query, llm):
    """
    Determina si la búsqueda es sobre un PRODUCTO (Filtro estricto) 
    o una VIBE/OCASIÓN (Filtro flexible).
    """
    cache_key = f"analysis_{query.lower().strip()}"
    cached = cache.get_json("analysis", cache_key)
    if cached: return cached

    template = """
    Analiza la intención de búsqueda: "{query}".
    
    1. Determina el TIPO: 
       - "PRODUCTO": Si busca una comida específica, ingrediente o plato (ej: flan, sushi, hamburguesa, cerveza, sin tacc).
       - "VIBE": Si busca una ocasión, ambiente, estilo o concepto abstracto (ej: cita, romántico, amigos, barato, lindo, vista, tranquilo).
    
    2. Genera KEYWORDS:
       - Si es PRODUCTO: Sinónimos directos (Flan -> flan, caramelo, crema).
       - Si es VIBE: Palabras que la gente usa en reseñas para describir eso (Cita -> pareja, íntimo, noche, romántico, ambiente).
    
    Responde SOLO JSON: {{"tipo": "PRODUCTO" o "VIBE", "keywords": ["k1", "k2"]}}
    """
    
    try:
        res = await llm.ainvoke(template)
        clean = res.content.strip().replace("```json", "").replace("```", "")
        data = json.loads(clean)
        
        # Agregamos la query original a las keywords
        if query.lower().strip() not in data['keywords']:
            data['keywords'].insert(0, query.lower().strip())
            
        cache.set_json("analysis", cache_key, data)
        return data
    except Exception as e:
        logger.error(f"Error analisis semantico: {e}")
        # Ante la duda, asumimos VIBE para no filtrar de más
        return {"tipo": "VIBE", "keywords": [query.lower()]}

def detectar_mencion_exacta(query, df):
    """
    Intenta detectar si el usuario nombró un local, manejando variaciones como:
    - Nombre completo: "Restaurante El Ciervo"
    - Nombre núcleo: "El Ciervo" (sin el prefijo 'Restaurante')
    - Nombre corto: "Ciervo" (sin artículos, con cuidado de blacklists)
    """
    if df is None or df.empty: return None
    q_norm = query.lower().strip()
    
    # 1. Lista de tipos de local para ignorar (Prefijos)
    venue_prefixes = {
        "restaurante", "parrilla", "bar", "confiteria", "pizzeria", "bodegon", 
        "cerveceria", "hamburgueseria", "heladeria", "cafe", "bistro", "resto", 
        "rotiseria", "panaderia", "sushi", "casa"
    }
    
    # 2. Artículos / Conectores para ignorar en la búsqueda de "palabra única"
    stopwords = {"el", "la", "los", "las", "de", "del", "lo", "al", "y", "en"}
    
    # 3. Lista negra de palabras que, si quedan solas, NO son un local
    # (Ej: Si un local se llama "La Comida", y queda "Comida", no queremos matchear).
    generic_blocklist = {
        "sushi", "pizza", "burger", "hamburguesa", "helado", "birra", 
        "cerveza", "cafe", "café", "parrilla", "pasta", "milanesa", 
        "ensalada", "comida", "postre", "resto", "bar", "almuerzo", "cena",
        "ciervo", "trucha", "cordero" # Agregamos carnes comunes si hay riesgo
    }
    # NOTA: "ciervo" está en blacklist por si buscan el animal, 
    # PERO la lógica de "Nombre Núcleo" (abajo) salvará a "El Ciervo".

    nombres = df['restaurante'].unique().tolist()
    # Ordenamos por largo para priorizar "El Ciervo" sobre "Ciervo"
    nombres.sort(key=len, reverse=True)

    for nombre_real in nombres:
        nombre_lower = nombre_real.lower().strip()
        
        # --- ESTRATEGIA A: Coincidencia Exacta del DB ---
        if nombre_lower in q_norm:
            return nombre_real

        # --- ESTRATEGIA B: Coincidencia del "Nombre Núcleo" (Sin Restaurante/Parrilla) ---
        # Ej: "Restaurante El Ciervo" -> "El Ciervo"
        parts = nombre_lower.split()
        
        # Filtramos los prefijos de tipo de local
        core_parts = [p for p in parts if re.sub(r'[^\w]', '', p) not in venue_prefixes]
        
        if not core_parts: continue # Si el local se llamaba "Restaurante", lo saltamos
        
        core_name = " ".join(core_parts) # "el ciervo"
        
        # Verificamos si el núcleo está en la query (con bordes de palabra)
        # Usamos regex para que "el ciervo" matchee "que tal es el ciervo"
        if len(core_name) > 2:
            pattern = r'(?<!\w)' + re.escape(core_name) + r'(?!\w)'
            if re.search(pattern, q_norm):
                return nombre_real

        # --- ESTRATEGIA C: Coincidencia de Palabra Distintiva ---
        # Ej: "El Ciervo" -> "Ciervo" (Solo si no está en blacklist)
        # Esto sirve para "Vamos a Growler" (Nombre real: Growler Station)
        
        # Quitamos stopwords del núcleo
        distinctive_parts = [p for p in core_parts if p not in stopwords]
        
        if distinctive_parts:
            dist_name = distinctive_parts[0] # Tomamos la primera palabra fuerte
            dist_clean = re.sub(r'[^\w]', '', dist_name)
            
            # Solo si es una palabra larga y NO es genérica
            if len(dist_clean) > 3 and dist_clean not in generic_blocklist:
                pattern_dist = r'(?<!\w)' + re.escape(dist_clean) + r'(?!\w)'
                if re.search(pattern_dist, q_norm):
                    return nombre_real

    return None

async def clasificar_intencion(query, llm, match_db=None):
    pista_contexto = ""
    if match_db:
        pista_contexto = (
            f"⚠️ NOTA CRÍTICA: He detectado que el usuario menciona EXACTAMENTE el nombre de un local: '{match_db}'. "
            f"Aunque '{match_db}' parezca un nombre de comida o animal, DEBES clasificarlo como 'SPECIFIC_INFO'."
        )

    template = f"""
    Clasifica la intención del usuario. QUERY: "{{query}}"
    {pista_contexto}

    OPCIONES:
    1. "STATS": El usuario pide cantidades, números o estadísticas.
    2. "SPECIFIC_INFO": El usuario pregunta por un LUGAR específico (ej: "opiniones de Growler", "Atu Sushi").
    3. "RECOMMENDATION": El usuario busca COMIDA, LUGAR o SUGERENCIAS gastronómicas (ej: "dónde comer helado", "lugar para cita", "barato").
    4. "IRRELEVANT": La consulta es incoherente, ofensiva, no tiene sentido semántico, o NO tiene relación con comida/restaurantes/salidas en Neuquén (ej: "precio del dolar", "comer personas", "asdasd", "quien es messi").

    Responde SOLO JSON: {{"intent": "...", "entity": "..."}} 
    """
    try:
        chain = ChatPromptTemplate.from_template(template) | llm | StrOutputParser()
        res_str = await chain.ainvoke({"query": query})
        clean_json = res_str.strip().replace("```json", "").replace("```", "")
        return json.loads(clean_json)
    except:
        # Ante la duda, si falla el JSON, lo mandamos a RAG por si acaso, 
        # o podés poner "IRRELEVANT" si preferís ser conservador.
        return {"intent": "RECOMMENDATION", "entity": None}

async def consultar_estadisticas(query, df, llm):
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
    if not query_str: return "Nombre vacío.", None, "", None
    q_clean = query_str.lower().strip()
    encontrados = []
    
    if es_seleccion_directa:
        mask_exact = df['restaurante'].str.lower() == q_clean
        if mask_exact.any():
            row = df[mask_exact].iloc[0]
            encontrados = [row['restaurante']]
    else:
        mask = df['restaurante'].str.lower().str.contains(q_clean, na=False, regex=False)
        candidatos = df[mask]['restaurante'].unique().tolist()
        if len(candidatos) == 1:
            encontrados = candidatos
        elif len(candidatos) > 1:
            encontrados = candidatos
            encontrados.sort()
            labels = []
            keys = []
            for r in encontrados:
                keys.append(r)
                mask_r = df['restaurante'] == r
                rowr = df[mask_r].iloc[0]
                ubi = safe_str(rowr.get('zona')) or safe_str(rowr.get('barrio')) or safe_str(rowr.get('direccion'))
                display = f"**{r}**" + (f" ({ubi})" if ubi else "")
                labels.append(display)
            lista_txt = "\n".join([f"{i+1}. {lbl}" for i, lbl in enumerate(labels)])
            prefix = "Encontré varios lugares con ese nombre. ¿A cuál te referís?"
            if tone in ('sassy', 'soberbio'): prefix = "Hay varios. ¿Cuál querés?"
            return f"{prefix}\n\n{lista_txt}\n\n*(Escribí el número)*", None, "", {"options": keys}

    if not encontrados: 
        return "No conozco ese lugar che, disculpá.", None, "", None
    
    restaurante = encontrados[0]
    cache_key = f"{restaurante}_{topic}_{sanitize_tone(tone)}" if topic else f"{restaurante}__{sanitize_tone(tone)}"
    cached_text = cache.get_json("resumen_texto", cache_key)
    if cached_text:
        return f"Acá te paso la data de **{restaurante}**:", restaurante, cached_text, None

    sorted_reviews = rankear_reviews_por_topico(df[df['restaurante'] == restaurante], topic)
    reviews_txt = "\n".join([safe_str(r.get('texto'))[:200] for _, r in sorted_reviews.head(10).iterrows()])
    
    contexto_tema = f"El usuario pregunta específicamente sobre: '{topic}'. Resalta eso." if topic else ""
    tone_prefix = tone_system_instruction(tone)
    tpl = f"""{tone_prefix}\nAnaliza: {{rest}}. Rating: {{rat}}. {contexto_tema}
    Reviews: {{revs}}
    Generá resumen Markdown (Argentino):
    ## 📊 La onda
    ## 👍 Lo bueno
    ## 💡 A mejorar
    ## 🎯 Veredicto"""
    
    try:
        res = await (ChatPromptTemplate.from_template(tpl) | llm | StrOutputParser()).ainvoke({
            "rest": restaurante, 
            "rat": safe_float(df[df['restaurante'] == restaurante].iloc[0].get('rating_gral')),
            "revs": reviews_txt
        })
    except: res = "No pude generar el resumen."
    
    cache.set_json("resumen_texto", cache_key, res)
    return f"¡Dale! Acá la data de **{restaurante}**:", restaurante, res, None

# ==========================================
# 5. ROUTER PRINCIPAL (ROUTER)
# ==========================================
async def procesar_consulta(query, df, vectorstore, llm, ctx=None):
    if ctx is None: ctx = {}
    tone = sanitize_tone(ctx.get('tone'))
    
    # 0. VERIFICACIÓN DE "BANEO" (Strike System)
    # Leemos el contador de strikes del contexto (si no existe, es 0)
    strikes = ctx.get('strikes', 0)
    
    if strikes >= 5:
        return "⛔ Sistema bloqueado por consultas incoherentes. Refrescá la página para empezar de nuevo.", "blocked", None, [], [], ""

    # 1. CONTEXTO NUMÉRICO (Igual que antes...)
    if 'pending_options' in ctx and query.strip().isdigit():
        # ... (código existente) ...
        # (Asegurate de devolver el contexto actualizado en el return de este bloque también si fuera necesario, 
        # pero por ahora lo dejamos simple).
        pass 

    # 2. PRE-SCAN (Igual que antes...)
    posible_match = detectar_mencion_exacta(query, df)

    # ... (Detección de stats igual que antes) ...
    # Supongamos que llegamos a la clasificación LLM
    
    # 3. CLASIFICACIÓN
    if es_pregunta_stats:
        intent = "STATS"
        entity = None
    else:
        clasificacion = await clasificar_intencion(query, llm, match_db=posible_match)
        intent = clasificacion.get("intent")
        entity = clasificacion.get("entity")

    # === BLOQUE DE INCOHERENCIAS (Strike +1) ===
    if intent == "IRRELEVANT":
        # Aumentamos el contador
        strikes += 1
        ctx['strikes'] = strikes # Actualizamos el contexto
        
        if strikes >= 5:
            return "⛔ Has alcanzado el límite de preguntas fuera de tópico. Refrescá para reiniciar.", "blocked", None, [], [], ""
            
        mensajes_error = [
            f"Mmm, no entendí. Preguntame sobre comida. (Advertencia {strikes}/5)",
            f"Epa, eso no suena a comida. Tirame una data de restaurantes. (Advertencia {strikes}/5)",
            f"No se entendió che. Centrémonos en morfar. (Advertencia {strikes}/5)"
        ]
        import random
        rta = random.choice(mensajes_error)
        
        # Devolvemos la respuesta y el contexto actualizado con el strike nuevo
        return rta, "rag", None, [], [], ""
    
    # 4. OVERRIDE
    if intent == "RECOMMENDATION" and posible_match:
        logger.info(f"🔄 Override: LLM dijo Recommendation, pero '{posible_match}' es un local real.")
        intent = "SPECIFIC_INFO"
        entity = posible_match

    # 5. STATS
    if intent == "STATS":
        # ... (Tu código existente de stats) ...
        resp, locales = await consultar_estadisticas(query, df, llm)
        cards = obtener_restaurant_cards_simple(locales, df)
        locs = obtener_coordenadas(locales, df)
        return resp, "estadisticas", None, locs, cards, ""

    # ... (El resto de las intenciones INFO ESPECIFICA y RAG siguen igual) ...
    
    # 6. INFO ESPECÍFICA
    if intent == "SPECIFIC_INFO":
        target = entity
        if not target and ctx.get('last_entity'): target = ctx['last_entity']
        if target:
            target_clean = target.lower().strip()
            match_exists = df['restaurante'].str.lower().str.contains(target_clean, na=False, regex=False).any()
            if not match_exists:
                logger.info(f"⚠️ Entity '{target}' no existe. Switch a RAG.")
                intent = "RECOMMENDATION" 
            else:
                ctx['original_query'] = query
                resp, nombre_real, det, opciones = await resumir_opiniones_local(target, df, llm, query, tone)
                if opciones: return resp, "resumen", opciones, [], [], ""
                if nombre_real:
                    ctx['last_entity'] = nombre_real
                    cards = await obtener_restaurant_cards([nombre_real], df, llm, query, tone)
                    locs = obtener_coordenadas([nombre_real], df)
                    return resp, "resumen", None, locs, cards, det
                intent = "RECOMMENDATION"

    # 7. RAG (RECOMENDACIÓN BLINDADA)
    if intent == "RECOMMENDATION":
        # ... (Tu bloque RAG actualizado que te pasé antes con analizar_query_semantica) ...
        # (Asegúrate de mantener el bloque RAG que te pasé en la respuesta anterior)
        try:
            docs = vectorstore.similarity_search(query, k=25)
            seen = set()
            locales_preliminares = []
            for d in docs:
                nom = d.metadata.get('nombre')
                if nom and nom not in seen:
                    seen.add(nom)
                    locales_preliminares.append(nom)
            
            analisis = await analizar_query_semantica(query, llm)
            tipo_busqueda = analisis.get("tipo", "VIBE")
            keywords = analisis.get("keywords", [])
            
            locales_confirmados = []
            if tipo_busqueda == "PRODUCTO":
                for local in locales_preliminares:
                    mask = df['restaurante'].str.lower() == local.lower()
                    if mask.any():
                        texto_gigante = " ".join(df[mask]['texto'].fillna("").astype(str).str.lower())
                        for k in keywords:
                            if k in texto_gigante:
                                locales_confirmados.append(local)
                                break
            else:
                locales_confirmados = locales_preliminares
            
            locales_confirmados = locales_confirmados[:5]

            if not locales_confirmados:
                 return "No encontré lugares que coincidan con tu búsqueda en Neuquén.", "rag", None, [], [], ""

            es_estricto = (tipo_busqueda == "PRODUCTO")
            
            cards = await obtener_restaurant_cards(
                locales_confirmados, df, llm, query, tone, 
                strict_mode=es_estricto, keywords_list=keywords
            )
            
            nombres_finales = [c.nombre for c in cards]
            
            if not nombres_finales:
                 return "Encontré referencias pero no pude confirmar la info detallada.", "rag", None, [], [], ""

            locs = obtener_coordenadas(nombres_finales, df)
            
            prefix = tone_system_instruction(tone)
            prompt_rag = (
                f"{prefix}\n"
                f"El usuario busca: '{query}' en NEUQUÉN CAPITAL.\n"
                f"He verificado estos lugares en mi base de datos: {', '.join(nombres_finales)}.\n"
                "INSTRUCCIONES:\n"
                "1. Recomienda SOLO los lugares de la lista de arriba.\n"
                "2. NO inventes lugares de Buenos Aires u otras ciudades.\n"
                "3. Explica brevemente por qué coinciden con la búsqueda."
            )
            rag_resp = await llm.ainvoke(prompt_rag)
            return rag_resp.content, "rag", None, locs, cards, ""
            
        except Exception as e:
            logger.error(f"Error RAG: {e}")
            return "Tuve un problema técnico buscando eso.", "rag", None, [], [], ""

    return "No entendí bien qué buscás, ¿podés reformular?", "rag", None, [], [], ""

# ==========================================
# 6. ENDPOINTS
# ==========================================

@app.get("/")
def read_root(): return {"status": "online", "message": "API OK v5.5"}

@app.get("/health")
def health_check(): return {"status": "healthy", "df_size": len(df) if df is not None else 0}

@app.get("/restaurant/{nombre}", response_model=RestaurantDetail)
async def get_restaurant_detail(nombre: str, topic: Optional[str] = None, tone: Optional[str] = None):
    global df, llm
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
            res = await llm.ainvoke(prompt_txt)
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
async def chat(req: QueryRequest):
    try:
        ctx = req.conversation_context.copy() if req.conversation_context else {}
        if req.tone: ctx['tone'] = sanitize_tone(req.tone)
        resp, mode, pend, locs, cards, det = await procesar_consulta(req.query, df, vectorstore, llm, ctx)
        
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