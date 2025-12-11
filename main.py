import os
import json
import re
import pandas as pd
import numpy as np
from typing import List, Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_pinecone import PineconeVectorStore
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from upstash_redis import Redis

# ==========================================
# 1. CONFIGURACIÓN E INFRAESTRUCTURA
# ==========================================
PINECONE_INDEX_NAME = os.getenv("PINECONE_INDEX_NAME", "que-morfamos-nqn")
ARCHIVO_DATASET = "dataset_reviews.parquet"
UPSTASH_REDIS_REST_URL = os.getenv("UPSTASH_REDIS_REST_URL")
UPSTASH_REDIS_REST_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN")

# --- GESTOR DE CACHÉ (REDIS) ---
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
        return key.lower().strip().replace(" ", "_")

    def get_json(self, prefix, key):
        if not self.client: return None
        full_key = f"{prefix}:{self._sanitize_key(key)}"
        try:
            data = self.client.get(full_key)
            if data:
                if isinstance(data, dict): return data
                return json.loads(data)
            return None
        except: return None

    def set_json(self, prefix, key, value_dict, expire=604800):
        if not self.client: return
        full_key = f"{prefix}:{self._sanitize_key(key)}"
        try:
            json_str = json.dumps(value_dict, ensure_ascii=False)
            self.client.set(full_key, json_str, ex=expire)
        except Exception as e: print(f"Error escritura caché: {e}")

cache = RedisCacheManager(UPSTASH_REDIS_REST_URL, UPSTASH_REDIS_REST_TOKEN)

# --- INICIALIZACIÓN APP ---
app = FastAPI(title="Que Morfamos API (Final)", version="3.5.0")

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

# ==========================================
# 2. MODELOS DE DATOS (PYDANTIC)
# ==========================================
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
    conversation_context: dict = {}

class QueryResponse(BaseModel):
    response: str
    mode: str
    conversation_context: dict = {}
    locations: list = []
    restaurant_cards: List[RestaurantCard] = []
    detail_content: str = ""

# ==========================================
# 3. HELPERS Y CARGA DE DATOS
# ==========================================
@app.on_event("startup")
async def startup_event():
    global df, vectorstore, llm
    print("☁️ Iniciando servidor...")
    
    if os.path.exists(ARCHIVO_DATASET):
        try:
            df = pd.read_parquet(ARCHIVO_DATASET)
            if 'restaurante' in df.columns:
                df['restaurante'] = df['restaurante'].str.strip()
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

# --- LIMPIEZA DE DATOS (ANTI-CRASH) ---
def safe_str(val):
    if pd.isna(val) or val is None: return ""
    return str(val).strip()

def safe_float(val):
    if pd.isna(val) or val is None: return 0.0
    try: return float(val)
    except: return 0.0

def safe_int(val):
    if pd.isna(val) or val is None: return 0
    try: return int(float(val))
    except: return 0

def formatear_autor(nombre):
    nombre = safe_str(nombre)
    if not nombre: return "Anónimo"
    partes = nombre.split()
    return f"{partes[0]} {partes[1][0]}." if len(partes) > 1 else partes[0]

def fecha_a_orden(fecha_str):
    fecha_str = safe_str(fecha_str).lower()
    if not fecha_str: return 9999
    numeros = re.findall(r'\d+', fecha_str)
    num = int(numeros[0]) if numeros else 1
    if 'hora' in fecha_str: return num
    if 'día' in fecha_str or 'dia' in fecha_str: return num * 24
    if 'semana' in fecha_str: return num * 168
    if 'mes' in fecha_str: return num * 720
    if 'año' in fecha_str: return num * 8760
    return 5000

def obtener_coordenadas(nombres, df):
    locs = []
    for nom in nombres:
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
# 4. LÓGICA DE NEGOCIO (CARDS, RESUMEN, CLASIFICACIÓN)
# ==========================================

def obtener_restaurant_cards_simple(nombres_restaurantes, df):
    """Versión rápida para estadísticas"""
    cards = []
    for nombre in nombres_restaurantes:
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

def obtener_restaurant_cards(nombres_restaurantes, df, llm):
    """Versión completa con descripciones generadas y cacheadas"""
    cards = []
    for nombre in nombres_restaurantes:
        mask = df['restaurante'].str.lower() == nombre.lower()
        if mask.any():
            rest_df = df[mask]
            row = rest_df.iloc[0]
            nombre_real = safe_str(row['restaurante'])

            frase = ""
            autor = ""
            reseñas_validas = rest_df[rest_df['texto'].notna() & (rest_df['texto'].str.len() > 50)]
            if len(reseñas_validas) > 0:
                r = reseñas_validas.iloc[0]
                frase = safe_str(r['texto'])[:120] + "..."
                autor = formatear_autor(r.get('autor'))

            # Descripción (Cacheada)
            desc = cache.get_json("desc", nombre_real)
            if not desc:
                sample = " ".join([safe_str(t) for t in rest_df['texto'].head(5)])[:800]
                try:
                    res = llm.invoke(f"Describe '{nombre_real}' en máx 12 palabras atractivas basado en: {sample}")
                    desc = res.content.strip().replace('"','')
                    cache.set_json("desc", nombre_real, desc)
                except: desc = "Restaurante popular en Neuquén."

            cards.append(RestaurantCard(
                nombre=nombre_real,
                rating=safe_float(row.get('rating_gral')),
                total_reviews=safe_int(row.get('total_reviews_google')),
                direccion=safe_str(row.get('direccion')),
                barrio=safe_str(row.get('barrio')),
                zona=safe_str(row.get('zona')),
                descripcion=safe_str(desc),
                frase_destacada=safe_str(frase),
                autor_reseña=safe_str(autor)
            ))
    return cards

def clasificar_intencion(query, llm):
    """Traffic Cop: Decide qué quiere el usuario usando LLM"""
    template = """
    Clasifica la intención del usuario.
    QUERY: "{query}"
    
    OPCIONES:
    1. "STATS": Preguntas de cantidad, totales (ej: "cuántas pizzerías", "total locales").
    2. "SPECIFIC_INFO": Pregunta sobre un lugar específico (ej: "opiniones de Atu", "qué onda el Club 5").
    3. "RECOMMENDATION": Sugerencias generales (ej: "dónde comer pasta", "lugar romántico").
    
    Responde JSON: {{"intent": "...", "entity": "..."}} (Entity es el nombre del lugar si aplica, sino null)
    """
    try:
        chain = ChatPromptTemplate.from_template(template) | llm | StrOutputParser()
        res_str = chain.invoke({"query": query}).strip().replace("```json", "").replace("```", "")
        return json.loads(res_str)
    except:
        return {"intent": "RECOMMENDATION", "entity": None}

def consultar_estadisticas(query, df, llm):
    # Extraer keyword para filtrar
    prompt = f"Extrae palabra clave para filtrar (ej: 'pizzerias' -> 'pizza'). Query: {query}. Solo la palabra."
    keyword = llm.invoke(prompt).content.strip().lower()
    
    total = df['restaurante'].nunique()
    if "total" in keyword or len(keyword) < 2:
        return f"Tengo registrados **{total}** restaurantes.", []
    
    mask = (df['restaurante'].str.lower().str.contains(keyword, na=False) | 
            df['texto'].str.lower().str.contains(keyword, na=False))
    locales_filtrados = df[mask]['restaurante'].unique().tolist()
    
    return f"Encontré **{len(locales_filtrados)}** lugares relacionados con '{keyword}'.", locales_filtrados

def resumir_opiniones_local(query_str, df, llm):
    """Genera resumen específico para el chat"""
    mask = df['restaurante'].str.lower().str.contains(query_str.lower(), na=False)
    encontrados = df[mask]['restaurante'].unique()
    
    if len(encontrados) == 0: return f"No encontré nada con '{query_str}'.", None, "", None
    
    # Ambigüedad
    if len(encontrados) > 1:
        lista_txt = "\n".join([f" {i+1}. {r}" for i, r in enumerate(encontrados)])
        return f"Encontré varias opciones:\n\n{lista_txt}\n\n¿Cuál decís? (Tirame el número)", None, "", list(encontrados)
    
    restaurante = encontrados[0]
    
    # Cache
    cached_text = cache.get_json("resumen_texto", restaurante)
    if cached_text:
        return f"¡Dale! Acá la data de **{restaurante}**:", restaurante, cached_text, None

    # Generar
    reviews_df = df[df['restaurante'] == restaurante]
    reviews_txt = "\n".join([safe_str(r.get('texto'))[:200] for _, r in reviews_df.head(10).iterrows()])
    
    tpl = """Analiza: {rest}. Rating: {rat}. Reviews: {revs}
    Generá resumen Markdown (Argentino):
    ## 📊 La onda
    ## 👍 Lo bueno
    ## 💡 A mejorar
    ## 🎯 Veredicto"""
    
    res = (ChatPromptTemplate.from_template(tpl) | llm | StrOutputParser()).invoke({
        "rest": restaurante, 
        "rat": safe_float(reviews_df.iloc[0].get('rating_gral')),
        "revs": reviews_txt
    })
    
    cache.set_json("resumen_texto", restaurante, res)
    return f"¡Dale! Acá la data de **{restaurante}**:", restaurante, res, None

# ==========================================
# 5. ROUTER PRINCIPAL (CEREBRO)
# ==========================================
def procesar_consulta(query, df, vectorstore, llm, ctx=None):
    if ctx is None: ctx = {}
    
    # 1. FLUJO FORZADO: CONTEXTO NUMÉRICO
    if 'pending_options' in ctx and query.strip().isdigit():
        num = int(query.strip())
        opciones = ctx['pending_options']
        if 1 <= num <= len(opciones):
            seleccion = opciones[num - 1]
            # Guardamos foco
            ctx['last_entity'] = seleccion
            
            resp, nombre_real, det, _ = resumir_opiniones_local(seleccion, df, llm)
            cards = obtener_restaurant_cards([nombre_real], df, llm)
            locs = obtener_coordenadas([nombre_real], df)
            return resp, "resumen", None, locs, cards, det
        else:
            return f"Elegí entre 1 y {len(opciones)}", "resumen", opciones, [], [], ""

    # 2. CLASIFICACIÓN INTELIGENTE
    clasificacion = clasificar_intencion(query, llm)
    intent = clasificacion.get("intent")
    entity = clasificacion.get("entity")
    
    print(f"🧠 Router: {intent} | Entity: {entity}")

    # 3. MODO ESTADÍSTICAS
    if intent == "STATS":
        resp, locales = consultar_estadisticas(query, df, llm)
        cards = obtener_restaurant_cards_simple(locales, df)
        locs = obtener_coordenadas(locales, df)
        return resp, "estadisticas", None, locs, cards, ""

    # 4. MODO INFORMACIÓN ESPECÍFICA
    if intent == "SPECIFIC_INFO":
        target = entity
        
        # Seguimiento de conversación (Ej: "y es caro?")
        if not target and ctx.get('last_entity'):
            target = ctx['last_entity']
            print(f"🔄 Usando contexto: {target}")

        if target:
            resp, nombre_real, det, opciones = resumir_opiniones_local(target, df, llm)
            
            if opciones: # Múltiples
                return resp, "resumen", opciones, [], [], ""
            
            if nombre_real: # Encontrado único
                ctx['last_entity'] = nombre_real # Actualizar foco
                cards = obtener_restaurant_cards([nombre_real], df, llm)
                locs = obtener_coordenadas([nombre_real], df)
                return resp, "resumen", None, locs, cards, det

    # 5. MODO RAG (RECOMENDACIÓN) - Default
    try:
        docs = vectorstore.similarity_search(query, k=15)
        # Extraer nombres únicos preservando orden
        seen = set()
        locales = []
        for d in docs:
            nom = d.metadata.get('nombre')
            if nom and nom not in seen:
                seen.add(nom)
                locales.append(nom)
        locales = locales[:5] # Top 5
        
        cards = obtener_restaurant_cards(locales, df, llm)
        locs = obtener_coordenadas(locales, df)
        
        # Generar respuesta amigable con el LLM
        prompt_rag = f"Usuario busca: '{query}'. Encontré estos lugares: {', '.join(locales)}. Recomendalos en 1 frase corta argentina."
        rag_resp = llm.invoke(prompt_rag).content
        
        return rag_resp, "rag", None, locs, cards, ""
        
    except Exception as e:
        print(f"Error RAG: {e}")
        return "Tuve un problema buscando eso. ¿Probamos de nuevo?", "rag", None, [], [], ""

# ==========================================
# 6. ENDPOINTS API
# ==========================================

@app.get("/")
def read_root():
    return {"status": "online", "message": "Backend funcionando OK 🚀"}

@app.get("/health")
def health_check():
    return {"status": "healthy", "df_size": len(df) if df is not None else 0}

@app.get("/restaurant/{nombre}", response_model=RestaurantDetail)
async def get_restaurant_detail(nombre: str):
    """Endpoint para modal/panel lateral"""
    global df, llm
    
    # 1. Buscar
    mask = df['restaurante'].str.lower() == nombre.lower()
    if not mask.any(): raise HTTPException(status_code=404, detail="No encontrado")
    
    rest_df = df[mask]
    row = rest_df.iloc[0]
    nombre_real = safe_str(row['restaurante'])
    
    # 2. Reseñas
    reviews_list = []
    con_texto = rest_df[rest_df['texto'].notna() & (rest_df['texto'].str.len() > 10)].copy()
    con_texto['orden'] = con_texto['fecha'].apply(fecha_a_orden)
    con_texto = con_texto.sort_values('orden')
    
    for _, r in con_texto.head(8).iterrows():
        reviews_list.append(ReviewDetail(
            autor=formatear_autor(r.get('autor')),
            rating=safe_int(r.get('rating_user')),
            texto=safe_str(r.get('texto'))[:300],
            fecha=safe_str(r.get('fecha'))
        ))

    # 3. Análisis JSON (Cacheado)
    analisis = cache.get_json("json_detail", nombre_real)
    if not analisis:
        sample = " | ".join([r.texto[:150] for r in reviews_list[:5]])
        prompt_txt = f"""Analiza "{nombre_real}" y responde SOLO JSON válido:
        {{"resumen": "descripción", "positivos": ["p1", "p2"], "negativos": ["n1"]}}
        Reseñas: {sample}"""
        try:
            res = llm.invoke(prompt_txt).content.strip().replace("```json","").replace("```","")
            analisis = json.loads(res)
            cache.set_json("json_detail", nombre_real, analisis)
        except: analisis = {"resumen": "Info no disponible", "positivos": [], "negativos": []}

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
def chat(req: QueryRequest):
    try:
        resp, mode, pend, locs, cards, det = procesar_consulta(
            req.query, df, vectorstore, llm, req.conversation_context
        )
        
        # Gestión de contexto
        new_ctx = req.conversation_context.copy()
        if pend: new_ctx['pending_options'] = pend
        elif 'pending_options' in new_ctx: del new_ctx['pending_options']
        
        # Persistir la entidad enfocada si existe en el contexto viejo
        if 'last_entity' in req.conversation_context and 'last_entity' not in new_ctx:
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
