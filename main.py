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

app = FastAPI(title="Que Morfamos API (Semantic)", version="5.3.0")

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
            
            # Normalize ascii columns for search (remove accents/diacritics)
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
    if tone == 'cordial':
        return 'Sos un asistente cordial, educado y respetuoso. Responde de forma clara y amable y con acento argentino.'
    if tone == 'soberbio':
        return 'Sos un asistente con tono soberbio y pedante — respuestas seguras, elocuentes y presuntuosas, y con acento argentino.'
    if tone == 'sassy':
        return 'Sos un asistente, irónico y con humor mordaz (sassy), con respuestas directas y breves, y con acento argentino.'
    return 'Sos un asistente, cordial, educado y respetuoso. Responde de forma clara y amable, y con acento argentino.'

# ==========================================
# 3. LÓGICA DE NEGOCIO (FILTRO TEMÁTICO)
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
    
    # Aseguramos que rating_user sea int
    if 'rating_user' in df_local.columns:
        df_local['rating_user'] = pd.to_numeric(df_local['rating_user'], errors='coerce').fillna(0).astype(int)
    else:
        df_local['rating_user'] = 0

    if not topic or len(topic) < 3:
        # Si no hay tópico, priorizamos las más recientes que tengan buen rating
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
        
        # LÓGICA DE SENTIMIENTO SIMPLE BASADA EN ESTRELLAS
        if match_found:
            if rating >= 4:
                score += 50  # Premiamos si habla del tema y es buena (Total 150)
            elif rating <= 2:
                score -= 20  # Penalizamos si habla del tema pero es mala (Total 80)
                # Sigue siendo > 0 porque es relevante, pero quedará abajo de las buenas.
        
        return score

    # Aplicamos la función a toda la fila (axis=1) para tener acceso al rating y al texto a la vez
    df_local['score_topic'] = df_local.apply(calcular_relevancia, axis=1)
    
    if df_local['score_topic'].max() == 0:
        return df_local.sort_values('orden_fecha')
        
    # Ordenamos: Mayor score primero
    return df_local.sort_values(['score_topic', 'orden_fecha'], ascending=[False, True])

def seleccionar_mejor_review(df_local, topic_query=None):
    # 1. Ranking normal (ya incluye ponderación de estrellas y keywords)
    sorted_df = rankear_reviews_por_topico(df_local, topic_query)
    
    if sorted_df.empty: return None

    # 2. ESCENARIO RAG (Búsqueda por tema)
    if topic_query:
        top_match = sorted_df.iloc[0]
        
        # Si el score es > 0, significa que encontró la palabra clave.
        if top_match['score_topic'] > 0:
            # VALIDACIÓN MÍNIMA:
            # Aceptamos reseñas cortas (ej: "Alto flan!"), pero filtramos 
            # basura extrema (menos de 4 chars, ej: "si", "ok", "no").
            texto = safe_str(top_match['texto'])
            if len(texto) >= 4:
                return top_match
            
        # Si el score es 0 (o menor), significa que NO encontró el tema.
        # Devolvemos None para que el "Patovica" en obtener_restaurant_cards
        # descarte este restaurante de los resultados.
        return None

    # 3. ESCENARIO INFO GENERAL (Sin tema específico)
    # Acá sí mantenemos el filtro de longitud para que la tarjeta se vea "bonita"
    # con una frase armada, y no diga simplemente "Excelente".
    candidatas = sorted_df[sorted_df['texto'].str.len() > 25] 
    
    if not candidatas.empty:
        return candidatas.iloc[0]
    
    # Si todas son cortas, devolvemos la primera (la más reciente/mejor rankeada)
    return sorted_df.iloc[0]

async def generar_descripcion_async(llm, nombre, sample, tone='cordial'):
    try:
        prefix = tone_system_instruction(tone)
        prompt = f"{prefix}\nDescribe '{nombre}' en máx 15 palabras atractivas basado en: {sample}"
        res = await llm.ainvoke(prompt)
        return res.content.strip().replace('"','')
    except:
        return "Restaurante popular en Neuquén."

async def obtener_restaurant_cards(nombres_restaurantes, df, llm, query_context=None, tone='cordial'):
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
            
            # 1. BUSCAMOS LA MEJOR RESEÑA (EL FILTRO)
            best_review = seleccionar_mejor_review(rest_df, query_context)
            
            # === PATOVICA (FILTRO DE RELEVANCIA) ===
            # Si hay un tema de búsqueda (RAG) y best_review es None,
            # significa que el restaurante no tiene NADA relevante sobre el tema.
            # Lo descartamos para evitar falsos positivos.
            if query_context and best_review is None:
                continue 
            # =======================================

            if best_review is not None:
                frase = safe_str(best_review['texto'])[:350] + "..."
                autor = formatear_autor(best_review.get('autor'))

            # 2. GESTIÓN DE DESCRIPCIONES (CACHE vs LLM)
            cache_key = f"{nombre_real}__{sanitize_tone(tone)}"
            desc = cache.get_json("desc", cache_key)
            
            if desc:
                tasks.append({"type": "cached", "val": desc, "row": row, "frase": frase, "autor": autor, "nombre_real": nombre_real})
            else:
                sample = " ".join([safe_str(t) for t in rest_df['texto'].head(5)])[:800]
                task_coro = generar_descripcion_async(llm, nombre_real, sample, tone)
                tasks.append({"type": "generate", "val": task_coro, "row": row, "frase": frase, "autor": autor, "nombre_real": nombre_real})

    # 3. EJECUCIÓN PARALELA DE GENERACIONES LLM
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

    # 4. REORDENAMIENTO INTELIGENTE POR DENSIDAD
    # Si estamos buscando un tema específico, ordenamos para que aparezcan primero
    # los "especialistas" (alto % de menciones) y no solo los populares.
    if query_context and len(cards) > 1:
        keywords = get_keywords_from_topic(query_context)
        
        if keywords:
            def calcular_score_densidad(card):
                # Filtramos el DF original para este restaurante
                mask_rest = df['restaurante'] == card.nombre
                reviews_rest = df[mask_rest]
                
                # Unimos texto para búsqueda rápida de keywords
                texto_gigante = " ".join(reviews_rest['texto'].fillna("").astype(str).str.lower())
                
                hits = 0
                for k in keywords:
                    hits += texto_gigante.count(k)
                
                # FÓRMULA DE SUAVIZADO: Score = Hits / (Total + 10)
                # El +10 evita que un lugar con 1 review y 1 hit tenga 100% de score.
                score = hits / (card.total_reviews + 10)
                
                # Bonus pequeño por rating alto para desempatar
                if card.rating >= 4.5: score *= 1.1
                
                return score

            # Ordenamos in-place (Mayor score primero)
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

async def expandir_query_con_llm(query, llm):
    cache_key = f"expansion_{query.lower().strip()}"
    cached = cache.get_json("keywords", cache_key)
    if cached: return cached

    # CAMBIO: Prompt mucho más estricto para evitar "postre" si busco "flan"
    template = """
    Actúa como experto gastronómico. El usuario busca: "{query}".
    Genera una lista de 3 a 5 palabras clave SINÓNIMAS O ESPECÍFICAS.
    NO incluyas categorías generales (ej: si busca 'flan' NO digas 'postre').
    Si busca 'hamburguesa', NO digas 'comida rápida', di 'burger'.
    
    Responde SOLO las palabras separadas por coma, en minúsculas.
    """
    
    try:
        res = await llm.ainvoke(template)
        texto = res.content.lower().strip()
        keywords = [k.strip() for k in texto.split(',') if k.strip()]
        
        original = query.lower().strip()
        if original not in keywords:
            keywords.insert(0, original)
            
        cache.set_json("keywords", cache_key, keywords)
        return keywords
    except Exception as e:
        return [query.lower().strip()]

def detectar_mencion_exacta(query, df):
    """
    1. Busca coincidencia exacta del nombre completo.
    2. Si falla, busca coincidencia de la PRIMERA palabra del nombre (para casos como 'Growler' vs 'Growler Station').
    """
    if df is None or df.empty: return None
    q_norm = query.lower().strip()
    
    # Normalización de stopwords para no matchear "La" de "La Nonna"
    stopwords_nombres = {"la", "el", "los", "las", "de", "del", "lo", "al"}
    
    nombres = df['restaurante'].unique().tolist()
    # Ordenar por largo descendente para prioridad (ej: 'Burger King' > 'Burger')
    nombres.sort(key=len, reverse=True)
    
    # 1. BÚSQUEDA EXACTA (Nombre Completo)
    for nombre in nombres:
        nombre_clean = nombre.lower().strip()
        if nombre_clean in q_norm:
            return nombre
            
    # 2. BÚSQUEDA POR PRIMERA PALABRA (Aproximación para "Growler", "Mostaza")
    # Solo si la primera palabra es distintiva (larga y no stopword)
    for nombre in nombres:
        parts = nombre.lower().split()
        if not parts: continue
        
        first_word = parts[0]
        # Limpieza de stopwords al inicio del nombre (ej: "El Biguá" -> "Biguá")
        if first_word in stopwords_nombres and len(parts) > 1:
            first_word = parts[1]
            
        # Solo consideramos palabras clave fuertes (>3 letras) para evitar falsos positivos
        if len(first_word) > 3:
            # Usamos regex boundary (\b) o simple 'in' con espacios para evitar que "Bar" matchee "Bariloche"
            # Pero para hacerlo simple y efectivo: chequeamos si está en la query
            if first_word in q_norm:
                # Devolvemos el nombre de la coincidencia (aunque sea parcial) 
                # para que el sistema sepa que hay un local candidato.
                return nombre 

    return None

async def clasificar_intencion(query, llm, match_db=None):
    # Inyectamos pista si Python encontró un nombre real
    pista_contexto = ""
    if match_db:
        pista_contexto = (
            f"⚠️ NOTA DE BASE DE DATOS: Existe un comercio registrado llamado '{match_db}'. "
            f"Si el usuario pregunta 'qué tal es {match_db}' o 'opiniones de {match_db}', "
            f"debes clasificarlo como 'SPECIFIC_INFO' y entity='{match_db}', aunque parezca un alimento."
        )

    template = f"""
    Clasifica la intención del usuario. QUERY: "{{query}}"
    
    {pista_contexto}

    OPCIONES:
    1. "STATS": El usuario pide cantidades, números o estadísticas (ej: "cuántos hay", "total de pizzerías").
    2. "SPECIFIC_INFO": El usuario pregunta por un LUGAR/COMERCIO específico por su NOMBRE PROPIO (ej: "opiniones de Antares", "dónde queda Atu").
    3. "RECOMMENDATION": El usuario tiene un antojo, busca un PLATO, un TIPO de comida o sugerencias generales (ej: "dónde comer helado", "mejor hamburguesa", "busco pastas").
    
    IMPORTANTE: Si el usuario pide un producto (ej: "helado de chocolate") y NO nombra un local, es "RECOMMENDATION".
    
    Responde SOLO JSON: {{"intent": "...", "entity": "..."}} 
    (Entity es el nombre del lugar si es SPECIFIC_INFO, sino null).
    """
    try:
        chain = ChatPromptTemplate.from_template(template) | llm | StrOutputParser()
        res_str = await chain.ainvoke({"query": query})
        clean_json = res_str.strip().replace("```json", "").replace("```", "")
        return json.loads(clean_json)
    except:
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
    """
    Busca un local. 
    - Si hay variantes (ej. 'Mostaza', 'Mostaza Shopping'), retorna menú de opciones.
    - Si es_seleccion_directa=True, asume que query_str es el nombre exacto elegido del menú.
    """
    if not query_str: return "Nombre vacío.", None, "", None
    
    q_clean = query_str.lower().strip()
    
    # Lista de candidatos
    encontrados = []
    
    if es_seleccion_directa:
        # Si viene de una selección de menú, confiamos en que el nombre es exacto
        # pero verificamos que exista por las dudas.
        mask_exact = df['restaurante'].str.lower() == q_clean
        if mask_exact.any():
            row = df[mask_exact].iloc[0]
            encontrados = [row['restaurante']]
    else:
        # Búsqueda difusa (.contains)
        mask = df['restaurante'].str.lower().str.contains(q_clean, na=False, regex=False)
        candidatos = df[mask]['restaurante'].unique().tolist()
        
        if len(candidatos) == 1:
            encontrados = candidatos
        elif len(candidatos) > 1:
            # Hay ambigüedad (Mostaza vs Mostaza Shopping) -> Devolvemos menú
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
            # Corregir comprobación: usar `in` para chequear múltiples valores
            if tone in ('sassy', 'soberbio'):
                prefix = "Hay varios. ¿Cuál querés?"
            
            resp_text = f"{prefix}\n\n{lista_txt}\n\n*(Escribí el número)*"
            return resp_text, None, "", {"options": keys}

    if not encontrados: 
        return "No conozco ese lugar che, disculpá.", None, "", None
    
    # --- Generación del Resumen (Si ya tenemos 1 solo candidato) ---
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
    
    # 1. CONTEXTO NUMÉRICO (Desambiguación)
    if 'pending_options' in ctx and query.strip().isdigit():
        num = int(query.strip())
        pending = ctx['pending_options']
        opciones = pending.get('options', []) if isinstance(pending, dict) else pending
        
        if 1 <= num <= len(opciones):
            seleccion = opciones[num - 1]
            ctx['last_entity'] = seleccion
            original_topic = ctx.get('original_query', seleccion) 
            
            # Llamamos con es_seleccion_directa=True para evitar loop de menú
            resp, nombre_real, det, _ = await resumir_opiniones_local(
                seleccion, df, llm, original_topic, tone, es_seleccion_directa=True
            )
            cards = await obtener_restaurant_cards([nombre_real], df, llm, original_topic, tone)
            locs = obtener_coordenadas([nombre_real], df)
            return resp, "resumen", None, locs, cards, det
        return f"Elegí entre 1 y {len(opciones)}", "resumen", pending, [], [], ""

    # 2. PRE-SCAN: "El Chismoso"
    posible_match = detectar_mencion_exacta(query, df)

    # 3. CLASIFICACIÓN
    clasificacion = await clasificar_intencion(query, llm, match_db=posible_match)
    intent = clasificacion.get("intent")
    entity = clasificacion.get("entity")
    logger.info(f"🧠 Router: {intent} | Entity: {entity} | DB Match: {posible_match}")
    
    # 4. OVERRIDE: Si LLM dice Recomendación pero hay match exacto (ej. Frambuesa y Chocolate), forzamos
    if intent == "RECOMMENDATION" and posible_match:
        logger.info(f"🔄 Override: LLM dijo Recommendation, pero '{posible_match}' es un local real.")
        intent = "SPECIFIC_INFO"
        entity = posible_match

    # 5. STATS
    if intent == "STATS":
        resp, locales = await consultar_estadisticas(query, df, llm)
        cards = obtener_restaurant_cards_simple(locales, df)
        locs = obtener_coordenadas(locales, df)
        return resp, "estadisticas", None, locs, cards, ""

    # 6. INFO ESPECÍFICA (CON FAIL-OVER)
    if intent == "SPECIFIC_INFO":
        target = entity
        if not target and ctx.get('last_entity'): target = ctx['last_entity']

        if target:
            # Validación: ¿Existe algo parecido en la DB?
            target_clean = target.lower().strip()
            match_exists = df['restaurante'].str.lower().str.contains(target_clean, na=False, regex=False).any()
            
            if not match_exists:
                # FAIL-OVER: Si no existe, asumimos que el LLM flasheó y es una comida (ej. Helado de Chocolate)
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
                
                # Fallback interno
                intent = "RECOMMENDATION"

    # 7. RAG (RECOMENDACIÓN)
    if intent == "RECOMMENDATION":
        try:
            # A. Búsqueda Vectorial (Trae candidatos por similitud semántica pura)
            docs = vectorstore.similarity_search(query, k=25)
            seen = set()
            locales_preliminares = []
            for d in docs:
                nom = d.metadata.get('nombre')
                if nom and nom not in seen:
                    seen.add(nom)
                    locales_preliminares.append(nom)
            
            # B. EL FILTRO INTELIGENTE (EXPANSIÓN + VALIDACIÓN)
            
            # 1. Expandimos la query usando el LLM
            # Ej: "Asiatica" -> ['asiatica', 'sushi', 'wok', 'china', 'japonesa']
            keywords_expandidas = await expandir_query_con_llm(query, llm)
            
            locales_confirmados = []
            
            if not keywords_expandidas:
                locales_confirmados = locales_preliminares[:5]
            else:
                for local in locales_preliminares:
                    mask = df['restaurante'].str.lower() == local.lower()
                    if mask.any():
                        rest_df = df[mask]
                        # Unimos texto para buscar rápido
                        texto_gigante = " ".join(rest_df['texto'].fillna("").astype(str).str.lower())
                        
                        # Chequeamos si ALGUNA de las keywords expandidas está presente
                        match = False
                        for k in keywords_expandidas:
                            # Usamos ' in ' simple. Podrías usar regex boundaries \b para más precisión
                            if k in texto_gigante:
                                match = True
                                break # Con encontrar 1 coincidencia alcanza (ej. encontró "sushi")
                        
                        if match:
                            locales_confirmados.append(local)
                        else:
                            # Burger King muere acá porque no dice "sushi", "wok" ni "china"
                            logger.info(f"🗑️ Descartado: {local} (No matcheó con {keywords_expandidas})")

                locales_confirmados = locales_confirmados[:5]

            if not locales_confirmados:
                 return "Busqué lugares con eso, pero no encontré reseñas que confirmen que lo tienen.", "rag", None, [], [], ""

            # C. Generar Cards
            cards = await obtener_restaurant_cards(locales_confirmados, df, llm, query, tone)
            
            # REORDENAMIENTO POR DENSIDAD (Usando keywords expandidas)
            if len(cards) > 1:
                def calcular_score_densidad(card):
                    mask_rest = df['restaurante'] == card.nombre
                    hits = 0
                    reviews_rest = df[mask_rest]
                    texto_completo = " ".join(reviews_rest['texto'].fillna("").astype(str).str.lower())
                    
                    # Sumamos hits de TODAS las variantes
                    for k in keywords_expandidas:
                        hits += texto_completo.count(k)
                    
                    return hits / (card.total_reviews + 5)

                cards.sort(key=calcular_score_densidad, reverse=True)

            nombres_finales = [c.nombre for c in cards]
            
            # ... (Resto del código igual: prompt final y retorno) ...
            locs = obtener_coordenadas(nombres_finales, df)
            prefix = tone_system_instruction(tone)
            prompt_rag = (
                f"{prefix}\nUsuario pregunta: '{query}'.\n"
                f"Lugares VERIFICADOS: {', '.join(nombres_finales)}.\n"
                "Recomendalos explicando qué plato tienen relacionado a la búsqueda."
            )
            rag_resp = await llm.ainvoke(prompt_rag)
            return rag_resp.content, "rag", None, locs, cards, ""

        except Exception as e:
            logger.error(f"Error RAG: {e}")
            return "Tuve un problema buscando eso.", "rag", None, [], [], ""
        
# ==========================================
# 6. ENDPOINTS
# ==========================================

@app.get("/")
def read_root(): return {"status": "online", "message": "API OK v5.3"}

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
        if req.tone:
            ctx['tone'] = sanitize_tone(req.tone)
        resp, mode, pend, locs, cards, det = await procesar_consulta(
            req.query, df, vectorstore, llm, ctx
        )
        
        new_ctx = ctx.copy() if ctx else {}
        if pend: 
            new_ctx['pending_options'] = pend
        elif 'pending_options' in new_ctx: 
            del new_ctx['pending_options']
        
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