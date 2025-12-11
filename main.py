import os
import json
import re
import pandas as pd
import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_pinecone import PineconeVectorStore
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from upstash_redis import Redis

# --- CONFIGURACIÓN DE ENTORNO ---
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
app = FastAPI(title="Que Morfamos API (Cloud)", version="3.0.0")

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

# --- MODELOS PYDANTIC (Restaurados del original) ---
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

# --- STARTUP ---
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

# --- HELPERS DE LIMPIEZA (ANTI-CRASH) ---
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
    """Convierte fechas relativas de Google a número para ordenar"""
    fecha_str = safe_str(fecha_str).lower()
    if not fecha_str: return 9999
    
    numeros = re.findall(r'\d+', fecha_str)
    num = int(numeros[0]) if numeros else 1
    
    if 'hora' in fecha_str or 'hour' in fecha_str: return num
    if 'día' in fecha_str or 'dia' in fecha_str or 'day' in fecha_str: return num * 24
    if 'semana' in fecha_str or 'week' in fecha_str: return num * 168
    if 'mes' in fecha_str or 'month' in fecha_str: return num * 720
    if 'año' in fecha_str or 'year' in fecha_str: return num * 8760
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
                    "lat": lat, 
                    "lng": lng,
                    "direccion": safe_str(r.get('direccion')),
                    "rating": safe_float(r.get('rating_gral')),
                    "total_reviews": safe_int(r.get('total_reviews_google'))
                })
    return locs

# --- GENERACIÓN DE TARJETAS ---
def obtener_restaurant_cards(nombres_restaurantes, df, llm):
    cards = []
    for nombre in nombres_restaurantes:
        mask = df['restaurante'].str.lower() == nombre.lower()
        if mask.any():
            rest_df = df[mask]
            row = rest_df.iloc[0]
            nombre_real = safe_str(row['restaurante'])

            frase = ""
            autor = ""
            # Buscamos reseña con texto válido
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

# --- LÓGICA DE RESUMEN ---
def resumir_opiniones_local(query_str, df, llm):
    mask = df['restaurante'].str.lower().str.contains(query_str.lower(), na=False)
    encontrados = df[mask]['restaurante'].unique()
    
    if len(encontrados) == 0: return f"No encontré '{query_str}'.", None, ""
    if len(encontrados) > 1:
        lista = "\n".join([f" {i+1}. {r}" for i, r in enumerate(encontrados)])
        return f"Encontré varias opciones:\n\n{lista}\n\n¿Cuál querés?", None, ""
    
    restaurante = encontrados[0]
    
    # Cache
    cached = cache.get_json("resumen_texto", restaurante)
    if cached: return f"¡Dale! Info de **{restaurante}**:", restaurante, cached

    # Generar
    reviews_df = df[df['restaurante'] == restaurante]
    reviews_txt = "\n".join([safe_str(r.get('texto'))[:200] for _, r in reviews_df.head(10).iterrows()])
    
    tpl = """Analiza: {rest}. Rating: {rat}. Reviews: {revs}
    Generá resumen Markdown (Argentino):
    ## 📊 La onda
    ## 👍 Lo bueno
    ## 💡 A mejorar
    ## 🎯 Veredicto"""
    
    prompt = ChatPromptTemplate.from_template(tpl)
    chain = prompt | llm | StrOutputParser()
    res = chain.invoke({
        "rest": restaurante, 
        "rat": safe_float(reviews_df.iloc[0].get('rating_gral')),
        "revs": reviews_txt
    })
    
    cache.set_json("resumen_texto", restaurante, res)
    return f"¡Dale! Info de **{restaurante}**:", restaurante, res

# --- ENDPOINTS ---

@app.get("/")
def read_root():
    return {"status": "online", "msg": "API Que Morfamos OK"}

@app.get("/health")
def health_check():
    return {"status": "healthy", "df_size": len(df) if df is not None else 0}

@app.get("/restaurant/{nombre}", response_model=RestaurantDetail)
async def get_restaurant_detail(nombre: str):
    """Endpoint para la vista detallada (El que se rompió antes)"""
    global df, llm
    
    # 1. Buscar restaurante
    mask = df['restaurante'].str.lower() == nombre.lower()
    if not mask.any():
        raise HTTPException(status_code=404, detail="No encontrado")
    
    rest_df = df[mask]
    row = rest_df.iloc[0]
    nombre_real = safe_str(row['restaurante'])
    
    # 2. Reseñas (Ordenadas por fecha)
    reviews_list = []
    con_texto = rest_df[rest_df['texto'].notna() & (rest_df['texto'].str.len() > 10)].copy()
    con_texto['orden'] = con_texto['fecha'].apply(fecha_a_orden)
    con_texto = con_texto.sort_values('orden') # Menor es más reciente
    
    for _, r in con_texto.head(8).iterrows():
        reviews_list.append(ReviewDetail(
            autor=formatear_autor(r.get('autor')),
            rating=safe_int(r.get('rating_user')),
            texto=safe_str(r.get('texto'))[:300],
            fecha=safe_str(r.get('fecha'))
        ))

    # 3. Análisis LLM (Con Caché JSON)
    analisis = cache.get_json("json_detail", nombre_real)
    
    if not analisis:
        # Generar si no existe
        sample = " | ".join([r.texto[:150] for r in reviews_list[:5]])
        prompt_txt = f"""Analiza "{nombre_real}" y responde SOLO JSON válido:
        {{"resumen": "2 oraciones descripción", "positivos": ["item1", "item2", "item3"], "negativos": ["item1", "item2"]}}
        Reseñas: {sample}"""
        
        try:
            res = llm.invoke(prompt_txt).content.strip().replace("```json","").replace("```","")
            analisis = json.loads(res)
            cache.set_json("json_detail", nombre_real, analisis)
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
def chat(req: QueryRequest):
    # Lógica simplificada de Router
    q_low = req.query.lower()
    
    # 1. Stats
    if any(p in q_low for p in ["cuantos", "cantidad", "total"]):
        total = df['restaurante'].nunique()
        return QueryResponse(response=f"Hay {total} locales.", mode="estadisticas")

    # 2. Resumen específico
    if "opiniones de" in q_low or "que onda" in q_low:
        for prep in [" de ", " sobre "]:
            if prep in q_low:
                target = q_low.split(prep, 1)[1].strip()
                resp, nom, det = resumir_opiniones_local(target, df, llm)
                if nom:
                    cards = obtener_restaurant_cards([nom], df, llm)
                    locs = obtener_coordenadas([nom], df)
                    return QueryResponse(response=resp, mode="resumen", locations=locs, restaurant_cards=cards, detail_content=det)

    # 3. RAG Default
    try:
        docs = vectorstore.similarity_search(req.query, k=15)
        locales = list(set([d.metadata.get('nombre') for d in docs]))[:5]
        
        cards = obtener_restaurant_cards(locales, df, llm)
        locs = obtener_coordenadas(locales, df)
        
        return QueryResponse(
            response="Acá tenés las mejores opciones que encontré:",
            mode="rag",
            locations=locs,
            restaurant_cards=cards
        )
    except Exception as e:
        print(f"Error chat: {e}")
        raise HTTPException(status_code=500, detail="Error procesando chat")

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
