import os
import json
import re
import asyncio
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
# 1. CONFIGURACIÓN
# ==========================================
PINECONE_INDEX_NAME = os.getenv("PINECONE_INDEX_NAME", "que-morfamos-nqn")
ARCHIVO_DATASET = "dataset_reviews.parquet"
UPSTASH_REDIS_REST_URL = os.getenv("UPSTASH_REDIS_REST_URL")
UPSTASH_REDIS_REST_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN")

# --- GESTOR DE CACHÉ ---
class RedisCacheManager:
    def __init__(self, url, token):
        self.client = None
        if url and token:
            try:
                self.client = Redis(url=url, token=token)
                print("✅ Redis (Upstash) conectado.")
            except Exception as e:
                print(f"⚠️ Error Redis: {e}")
        else:
            print("⚠️ Faltan credenciales Redis.")

    def _sanitize_key(self, key):
        if not key: return "unknown"
        return str(key).lower().strip().replace(" ", "_")

    def get_json(self, prefix, key):
        if not self.client: return None
        full_key = f"{prefix}:{self._sanitize_key(key)}"
        try:
            data = self.client.get(full_key)
            if data:
                return data if isinstance(data, dict) else json.loads(data)
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

# --- INICIALIZACIÓN APP ---
app = FastAPI(title="Que Morfamos API (Topic Aware)", version="4.0.0")

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
    print("☁️ Iniciando servidor...")
    
    if os.path.exists(ARCHIVO_DATASET):
        try:
            df = pd.read_parquet(ARCHIVO_DATASET)
            cols_to_fix = ['restaurante', 'texto', 'direccion', 'barrio', 'zona', 'autor', 'fecha']
            for col in cols_to_fix:
                if col in df.columns:
                    df[col] = df[col].fillna("").astype(str).str.strip()
            df['rating_gral'] = pd.to_numeric(df['rating_gral'], errors='coerce').fillna(0.0)
            print(f"✅ DataFrame cargado: {len(df)} filas.")
        except Exception as e:
            print(f"❌ Error leyendo Parquet: {e}")
            df = pd.DataFrame()
    else:
        print("⚠️ No se encontró dataset.")
        df = pd.DataFrame()

    try:
        embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
        vectorstore = PineconeVectorStore.from_existing_index(
            index_name=PINECONE_INDEX_NAME, embedding=embeddings
        )
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
        print("✅ IA (OpenAI + Pinecone) lista.")
    except Exception as e:
        print(f"❌ Error IA: {e}")

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

# ==========================================
# 3. LÓGICA DE NEGOCIO (FILTRO TEMÁTICO)
# ==========================================

def get_keywords_from_topic(topic):
    """Extrae palabras clave de la búsqueda del usuario"""
    if not topic: return []
    stopwords = {"de", "la", "el", "en", "y", "que", "los", "las", "un", "una", "del", "para", "con", "donde", "hay", "lugar", "lugares", "comer", "mejor", "mejores", "neuquen"}
    # Limpiamos y separamos
    words = safe_str(topic).lower().split()
    return [w for w in words if w not in stopwords and len(w) > 2]

def rankear_reviews_por_topico(df_reviews, topic=None):
    """
    Ordena las reseñas. Si hay topic, prioriza las que hablan de eso.
    Si no, prioriza fecha.
    """
    df_local = df_reviews.copy()
    
    # 1. Calcular Fecha (Base)
    df_local['orden_fecha'] = df_local['fecha'].apply(fecha_a_orden)
    
    # 2. Si NO hay topic, devolvemos por fecha (reciente primero)
    if not topic:
        return df_local.sort_values('orden_fecha')

    # 3. Si HAY topic, scoring por palabras clave
    keywords = get_keywords_from_topic(topic)
    if not keywords:
        return df_local.sort_values('orden_fecha') # Topic irrelevante, fallback

    pattern = "|".join(keywords)
    
    # Crear Score: 100 puntos si tiene palabra clave, -penalización por antigüedad
    # (Así priorizamos relevancia semántica sobre fecha, pero fecha desempata)
    
    def calcular_relevancia(texto):
        texto = safe_str(texto).lower()
        count = sum(1 for k in keywords if k in texto)
        return count * 100 # 100 puntos por cada palabra clave encontrada

    df_local['score_topic'] = df_local['texto'].apply(calcular_relevancia)
    
    # Ordenar: Mayor score primero, luego menor antigüedad
    return df_local.sort_values(['score_topic', 'orden_fecha'], ascending=[False, True])

def seleccionar_mejor_review(df_local, topic_query=None):
    """Helper para elegir frase destacada en cards"""
    sorted_df = rankear_reviews_por_topico(df_local, topic_query)
    candidatas = sorted_df[sorted_df['texto'].str.len() > 40]
    
    if not candidatas.empty:
        return candidatas.iloc[0]
    elif not sorted_df.empty:
        return sorted_df.iloc[0]
    return None

async def generar_descripcion_async(llm, nombre, sample):
    try:
        res = await llm.ainvoke(f"Describe '{nombre}' en máx 12 palabras atractivas basado en: {sample}")
        return res.content.strip().replace('"','')
    except:
        return "Restaurante popular en Neuquén."

async def obtener_restaurant_cards(nombres_restaurantes, df, llm, query_context=None):
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
            # Usamos el ranking inteligente
            best_review = seleccionar_mejor_review(rest_df, query_context)
            
            if best_review is not None:
                frase = safe_str(best_review['texto'])[:300] + "..."
                autor = formatear_autor(best_review.get('autor'))

            # Descripción (Cacheada en Redis)
            desc = cache.get_json("desc", nombre_real)
            if desc:
                tasks.append({"type": "cached", "val": desc, "row": row, "frase": frase, "autor": autor, "nombre_real": nombre_real})
            else:
                sample = " ".join([safe_str(t) for t in rest_df['texto'].head(5)])[:800]
                task_coro = generar_descripcion_async(llm, nombre_real, sample)
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
            cache.set_json("desc", item['nombre_real'], descripcion)

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

async def clasificar_intencion(query, llm):
    template = """
    Clasifica la intención. QUERY: "{query}"
    OPCIONES:
    1. "STATS": Cantidades, totales, cuantos hay.
    2. "SPECIFIC_INFO": Lugar específico (ej: "opiniones de Atu", "info de Antares").
    3. "RECOMMENDATION": Sugerencias generales.
    
    Responde JSON: {{"intent": "...", "entity": "..."}} (Entity null si no aplica)
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
        keyword = await llm.ainvoke(prompt) 
        keyword = keyword.content.strip().lower()
        keyword = re.sub(r'[^\w\s]', '', keyword)
        
        total = df['restaurante'].nunique()
        if "total" in keyword or len(keyword) < 2:
            return f"Tengo registrados **{total}** restaurantes.", []
        
        mask = (df['restaurante'].str.lower().str.contains(keyword, na=False) | 
                df['texto'].str.lower().str.contains(keyword, na=False))
        
        locales_filtrados = df[mask]['restaurante'].unique().tolist()
        return f"Encontré **{len(locales_filtrados)}** lugares relacionados con '{keyword}'.", locales_filtrados
    except: return "No pude calcular esa estadística.", []

async def resumir_opiniones_local(query_str, df, llm):
    if not query_str: return "Nombre vacío.", None, "", None
    
    q_clean = query_str.lower().strip()
    
    # 1. Match Exacto
    mask_exact = df['restaurante'].str.lower() == q_clean
    if mask_exact.any():
        restaurante = df[mask_exact].iloc[0]['restaurante']
        encontrados = [restaurante]
    else:
        # 2. Match Difuso
        mask = df['restaurante'].str.lower().str.contains(q_clean, na=False)
        encontrados = df[mask]['restaurante'].unique()
    
    if len(encontrados) == 0: 
        return "No conozco ese lugar che, disculpá.", None, "", None
    
    if len(encontrados) > 1:
        labels = []
        keys = []
        for r in encontrados:
            keys.append(r)
            mask_r = df['restaurante'].str.lower() == r.lower()
            extra = ""
            if mask_r.any():
                rowr = df[mask_r].iloc[0]
                extra = safe_str(rowr.get('direccion')) or safe_str(rowr.get('barrio')) or safe_str(rowr.get('zona'))
            display = f"{r} ({extra})" if extra else r
            labels.append(display)

        lista_txt = "\n".join([f" {i+1}. {lbl}" for i, lbl in enumerate(labels)])
        resp_text = f"Encontré varias opciones:\n\n{lista_txt}\n\n¿A cuál te referís? (Escribí el número)"
        return resp_text, None, "", {"options": keys}
    
    restaurante = encontrados[0]
    cached_text = cache.get_json("resumen_texto", restaurante)
    if cached_text:
        return f"¡Dale! Acá la data de **{restaurante}**:", restaurante, cached_text, None

    reviews_df = df[df['restaurante'] == restaurante]
    reviews_txt = "\n".join([safe_str(r.get('texto'))[:200] for _, r in reviews_df.head(10).iterrows()])
    
    tpl = """Analiza: {rest}. Rating: {rat}. Reviews: {revs}
    Generá resumen Markdown (Argentino):
    ## 📊 La onda
    ## 👍 Lo bueno
    ## 💡 A mejorar
    ## 🎯 Veredicto"""
    
    try:
        res = await (ChatPromptTemplate.from_template(tpl) | llm | StrOutputParser()).ainvoke({
            "rest": restaurante, 
            "rat": safe_float(reviews_df.iloc[0].get('rating_gral')),
            "revs": reviews_txt
        })
    except: res = "No pude generar el resumen."
    
    cache.set_json("resumen_texto", restaurante, res)
    return f"¡Dale! Acá la data de **{restaurante}**:", restaurante, res, None

# ==========================================
# 4. ROUTER PRINCIPAL (ASYNC)
# ==========================================
async def procesar_consulta(query, df, vectorstore, llm, ctx=None):
    if ctx is None: ctx = {}
    
    # 1. CONTEXTO NUMÉRICO
    if 'pending_options' in ctx and query.strip().isdigit():
        num = int(query.strip())
        pending = ctx['pending_options']
        opciones = pending.get('options', []) if isinstance(pending, dict) else pending
        
        if 1 <= num <= len(opciones):
            seleccion = opciones[num - 1]
            ctx['last_entity'] = seleccion
            resp, nombre_real, det, _ = await resumir_opiniones_local(seleccion, df, llm)
            cards = await obtener_restaurant_cards([nombre_real], df, llm, seleccion)
            locs = obtener_coordenadas([nombre_real], df)
            return resp, "resumen", None, locs, cards, det
        return f"Elegí entre 1 y {len(opciones)}", "resumen", pending, [], [], ""

    # 2. CLASIFICACIÓN
    clasificacion = await clasificar_intencion(query, llm)
    intent = clasificacion.get("intent")
    entity = clasificacion.get("entity")
    
    print(f"🧠 Router: {intent} | Entity: {entity}")

    # 3. STATS
    if intent == "STATS":
        resp, locales = await consultar_estadisticas(query, df, llm)
        cards = obtener_restaurant_cards_simple(locales, df)
        locs = obtener_coordenadas(locales, df)
        return resp, "estadisticas", None, locs, cards, ""

    # 4. INFO ESPECÍFICA
    if intent == "SPECIFIC_INFO":
        target = entity
        if not target and ctx.get('last_entity'): target = ctx['last_entity']

        if target:
            resp, nombre_real, det, opciones = await resumir_opiniones_local(target, df, llm)
            
            if opciones: return resp, "resumen", opciones, [], [], ""
            
            if nombre_real:
                ctx['last_entity'] = nombre_real
                cards = await obtener_restaurant_cards([nombre_real], df, llm, target)
                locs = obtener_coordenadas([nombre_real], df)
                return resp, "resumen", None, locs, cards, det
            
            return resp, "resumen", None, [], [], ""

    # 5. RAG (RECOMENDACIÓN)
    try:
        docs = vectorstore.similarity_search(query, k=15)
        seen = set()
        locales = []
        for d in docs:
            nom = d.metadata.get('nombre')
            if nom and nom not in seen:
                seen.add(nom)
                locales.append(nom)
        locales = locales[:5]
        
        # KEY CHANGE: Pasamos la query para buscar reseñas relevantes
        cards = await obtener_restaurant_cards(locales, df, llm, query)
        locs = obtener_coordenadas(locales, df)
        
        prompt_rag = (
            f"Usuario busca: '{query}'. Encontré: {', '.join(locales)}. "
            "Recomendalos en 1 frase corta para Neuquén."
        )
        rag_resp = await llm.ainvoke(prompt_rag)
        
        return rag_resp.content, "rag", None, locs, cards, ""
    except Exception as e:
        print(f"Error RAG: {e}")
        return "Tuve un problema buscando eso. ¿Probamos de nuevo?", "rag", None, [], [], ""

# ==========================================
# 5. ENDPOINTS
# ==========================================

@app.get("/")
def read_root(): return {"status": "online", "message": "API OK"}

@app.get("/health")
def health_check(): return {"status": "healthy", "df_size": len(df) if df is not None else 0}

@app.get("/restaurant/{nombre}", response_model=RestaurantDetail)
async def get_restaurant_detail(nombre: str, topic: Optional[str] = None):
    """
    Endpoint de detalles. 
    Acepta ?topic=... para filtrar reseñas por tema.
    """
    global df, llm
    if not nombre: raise HTTPException(status_code=404)
    
    mask = df['restaurante'].str.lower() == nombre.lower()
    if not mask.any(): raise HTTPException(status_code=404, detail="No encontrado")
    
    rest_df = df[mask]
    row = rest_df.iloc[0]
    nombre_real = safe_str(row['restaurante'])
    
    # 1. ORDENAR RESEÑAS POR TEMA O FECHA
    # Usamos la nueva lógica de ranking
    sorted_reviews = rankear_reviews_por_topico(rest_df, topic)
    
    # Tomamos las top 8
    reviews_list = []
    for _, r in sorted_reviews.head(8).iterrows():
        if len(safe_str(r.get('texto'))) > 10: # Filtro minimo
            reviews_list.append(ReviewDetail(
                autor=formatear_autor(r.get('autor')),
                rating=safe_int(r.get('rating_user')),
                texto=safe_str(r.get('texto'))[:2000],
                fecha=safe_str(r.get('fecha'))
            ))

    # 2. GENERAR ANÁLISIS LLM (Cacheado por TEMA)
    # La clave de caché ahora incluye el topic para no mezclar
    cache_key_suffix = f"json_detail:{nombre_real}"
    if topic:
        cache_key_suffix += f":{topic.lower().replace(' ', '_')}"
        
    analisis = cache.get_json(cache_key_suffix, "") # Usamos suffix como prefix o key directa
    # Simplificamos uso de cache wrapper:
    analisis = cache.get_json("detail_topic", f"{nombre_real}_{topic}" if topic else nombre_real)

    if not analisis:
        # Preparamos prompt temático
        sample = " | ".join([r.texto[:150] for r in reviews_list[:5]])
        
        contexto_tema = ""
        if topic:
            contexto_tema = f"ENFOCATE ESPECÍFICAMENTE en opiniones sobre: '{topic}'."
            intro_resumen = f"Sobre {topic}: "
        else:
            intro_resumen = ""

        prompt_txt = f"""Analiza "{nombre_real}". {contexto_tema}
        Responde SOLO JSON válido:
        {{"resumen": "{intro_resumen}descripción de 2 oraciones...", "positivos": ["p1", "p2"], "negativos": ["n1"]}}
        
        Reviews de usuarios: {sample}"""
        
        try:
            res = await llm.ainvoke(prompt_txt)
            clean = res.content.strip().replace("```json","").replace("```","")
            analisis = json.loads(clean)
            
            # Guardamos con la clave específica
            cache.set_json("detail_topic", f"{nombre_real}_{topic}" if topic else nombre_real, analisis)
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
        resp, mode, pend, locs, cards, det = await procesar_consulta(
            req.query, df, vectorstore, llm, req.conversation_context
        )
        
        new_ctx = req.conversation_context.copy() if req.conversation_context else {}
        if pend: 
            new_ctx['pending_options'] = pend
        elif 'pending_options' in new_ctx: 
            del new_ctx['pending_options']
        
        if req.conversation_context and 'last_entity' in req.conversation_context and 'last_entity' not in new_ctx:
             new_ctx['last_entity'] = req.conversation_context['last_entity']

        return QueryResponse(
            response=resp, mode=mode, conversation_context=new_ctx,
            locations=locs, restaurant_cards=cards, detail_content=det
        )
    except Exception as e:
        print(f"Error Chat: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)