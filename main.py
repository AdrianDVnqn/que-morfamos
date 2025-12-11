import os
import json
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_pinecone import PineconeVectorStore
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from upstash_redis import Redis

# --- CONFIGURACIÓN DE ENTORNO ---
PINECONE_INDEX_NAME = os.getenv("PINECONE_INDEX_NAME", "que-morfamos-nqn")
ARCHIVO_DATASET = "dataset_reviews.parquet"

# Variables de Upstash (Configuralas en Render)
UPSTASH_REDIS_REST_URL = os.getenv("UPSTASH_REDIS_REST_URL")
UPSTASH_REDIS_REST_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN")

# --- CLASE GESTORA DE REDIS (VERSIÓN UPSTASH HTTP) ---
class RedisCacheManager:
    def __init__(self, url, token):
        self.client = None
        if url and token:
            try:
                # Conexión vía HTTP (Ideal para Serverless/Render)
                self.client = Redis(url=url, token=token)
                print("✅ Cliente Upstash Redis configurado.")
            except Exception as e:
                print(f"⚠️ Error configurando Upstash: {e}")
                self.client = None
        else:
            print("⚠️ Faltan credenciales de Upstash (URL o TOKEN). Cache desactivada.")

    def _sanitize_key(self, key):
        """Limpia la clave"""
        return key.lower().strip().replace(" ", "_")

    def get_json(self, prefix, key):
        """Recupera un diccionario JSON desde Redis"""
        if not self.client: return None
        
        full_key = f"{prefix}:{self._sanitize_key(key)}"
        try:
            # Upstash devuelve el string directamente o None
            data = self.client.get(full_key)
            if data:
                print(f"⚡ Cache Hit (Redis): {full_key}")
                # Upstash a veces devuelve dict si ya es JSON válido, o string
                if isinstance(data, dict):
                    return data
                return json.loads(data)
            return None
        except Exception as e:
            print(f"Error leyendo caché: {e}")
            return None

    def set_json(self, prefix, key, value_dict, expire_seconds=604800):
        """Guarda un diccionario como JSON string. Expira en 1 semana."""
        if not self.client: return
        
        full_key = f"{prefix}:{self._sanitize_key(key)}"
        try:
            # Serializamos a string para asegurar formato
            json_str = json.dumps(value_dict, ensure_ascii=False)
            # Upstash usa 'ex' para segundos de expiración
            self.client.set(full_key, json_str, ex=expire_seconds)
            print(f"💾 Guardado en Redis: {full_key}")
        except Exception as e:
            print(f"Error escribiendo caché: {e}")

# Instancia global usando las variables nuevas
cache = RedisCacheManager(UPSTASH_REDIS_REST_URL, UPSTASH_REDIS_REST_TOKEN)

# --- INICIALIZACIÓN APP ---
app = FastAPI(title="Restaurant Chatbot API (Upstash Edition)", version="2.3.0")

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
class QueryRequest(BaseModel):
    query: str
    conversation_context: dict = {}

class QueryResponse(BaseModel):
    response: str
    mode: str
    conversation_context: dict = {}
    locations: list = []
    restaurant_cards: list = []
    detail_content: str = ""

# --- STARTUP ---
@app.on_event("startup")
async def startup_event():
    global df, vectorstore, llm
    print("☁️ Inicializando Backend...")
    
    # 1. Cargar Pandas
    if os.path.exists(ARCHIVO_DATASET):
        df = pd.read_parquet(ARCHIVO_DATASET, low_memory=False)
        df['restaurante'] = df['restaurante'].str.strip()
        print(f"✅ DataFrame cargado: {len(df)} reseñas.")
    else:
        print("⚠️ Parquet no encontrado. Modo fallback.")
        df = pd.DataFrame()

    # 2. Pinecone & OpenAI
    try:
        embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
        vectorstore = PineconeVectorStore.from_existing_index(
            index_name=PINECONE_INDEX_NAME, embedding=embeddings
        )
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
        print("✅ IA conectada (Pinecone + OpenAI).")
    except Exception as e:
        print(f"❌ Error crítico IA: {e}")

# --- HELPERS ---
def formatear_autor(nombre):
    if pd.isna(nombre) or not nombre: return "Anónimo"
    partes = str(nombre).strip().split()
    return f"{partes[0]} {partes[1][0]}." if len(partes) > 1 else partes[0]

def obtener_coordenadas(nombres, df):
    locs = []
    for nom in nombres:
        mask = df['restaurante'].str.lower() == nom.lower()
        if mask.any():
            r = df[mask].iloc[0]
            if pd.notna(r.get('latitud')):
                locs.append({
                    "nombre": r['restaurante'],
                    "lat": float(r['latitud']),
                    "lng": float(r['longitud']),
                    "direccion": str(r.get('direccion','')),
                    "rating": float(r.get('rating_gral',0)),
                    "total_reviews": int(r.get('total_reviews_google',0))
                })
    return locs

# --- GENERACIÓN DE TARJETAS (CON REDIS) ---
def obtener_restaurant_cards(nombres_restaurantes, df, llm):
    cards = []
    for nombre in nombres_restaurantes:
        mask = df['restaurante'].str.lower() == nombre.lower()
        if mask.any():
            rest_df = df[mask]
            row = rest_df.iloc[0]
            nombre_real = row['restaurante']

            # 1. Frase destacada (Rápido, desde Pandas)
            frase_destacada = ""
            autor_reseña = ""
            reseñas_validas = rest_df[rest_df['texto'].notna() & (rest_df['texto'].str.len() > 50)]
            if len(reseñas_validas) > 0:
                # Lógica simple para agarrar una reseña representativa
                # Intentamos agarrar una mediana para que no sea ni muy corta ni muy larga
                r = reseñas_validas.iloc[0] 
                frase_destacada = r['texto'][:120] + "..."
                autor_reseña = formatear_autor(r.get('autor'))

            # 2. Descripción Inteligente (CACHEADA EN REDIS)
            descripcion = cache.get_json("desc", nombre_real)
            
            if not descripcion:
                # Generar si no existe
                # print(f"🤖 Generando descripción nueva para {nombre_real}")
                reviews_txt = " ".join([str(t) for t in rest_df['texto'].head(5).tolist()])[:800]
                try:
                    prompt = f"Describe '{nombre_real}' en máx 12 palabras atractivas basado en: {reviews_txt}"
                    desc_str = llm.invoke(prompt).content.strip().replace('"','')
                    descripcion = desc_str
                    # Guardamos string directo, el wrapper lo hace JSON string
                    cache.set_json("desc", nombre_real, descripcion)
                except:
                    descripcion = "Restaurante popular en Neuquén."

            cards.append({
                "nombre": nombre_real,
                "rating": float(row.get('rating_gral', 0)),
                "total_reviews": int(row.get('total_reviews_google', 0)),
                "direccion": str(row.get('direccion', '')),
                "barrio": str(row.get('barrio', '')),
                "zona": str(row.get('zona', '')),
                "descripcion": descripcion, 
                "frase_destacada": frase_destacada,
                "autor_reseña": autor_reseña
            })
    return cards

# --- RESUMEN DETALLADO (CON REDIS) ---
def resumir_opiniones_local(query_str, df, llm):
    mask = df['restaurante'].str.lower().str.contains(query_str.lower(), na=False)
    encontrados = df[mask]['restaurante'].unique()
    
    if len(encontrados) == 0: return f"No encontré '{query_str}'.", None, ""
    if len(encontrados) > 1:
        lista = "\n".join([f"   {i+1}. {r}" for i, r in enumerate(encontrados)])
        return f"Encontré varias opciones:\n\n{lista}\n\n¿Cuál querés?", None, ""
    
    restaurante = encontrados[0]
    
    # 1. BUSCAR EN REDIS
    cache_data = cache.get_json("resumen", restaurante)
    if cache_data:
        return f"¡De una! Acá tenés la data de **{restaurante}**.", restaurante, cache_data

    # 2. GENERAR SI NO EXISTE
    print(f"🤖 Generando resumen detallado para {restaurante}...")
    reviews_df = df[df['restaurante'] == restaurante]
    reviews_text = "\n- ".join([f"{str(r.get('texto',''))[:200]}" for _, r in reviews_df.head(10).iterrows()])
    
    template = """
    Analiza: {restaurante}. Rating: {rating}
    Reseñas: {reviews}
    
    Generá un resumen Markdown (Español Argentino):
    ## 📊 La onda del lugar
    ## 👍 Lo mejor
    ## 💡 A tener en cuenta
    ## 🎯 Veredicto
    """
    prompt = ChatPromptTemplate.from_template(template)
    chain = prompt | llm | StrOutputParser()
    
    resumen_generado = chain.invoke({
        "restaurante": restaurante,
        "rating": reviews_df.iloc[0].get('rating_gral', 'N/A'),
        "reviews": reviews_text
    })
    
    # 3. GUARDAR EN REDIS
    cache.set_json("resumen", restaurante, resumen_generado)
    
    return f"¡De una! Acá tenés la data de **{restaurante}**.", restaurante, resumen_generado

# --- ROUTER ---
def procesar_consulta(query, df, vectorstore, llm, ctx=None):
    q_low = query.lower()
    
    # Opciones numéricas
    if ctx and 'pending_options' in ctx and query.strip().isdigit():
        num = int(query.strip())
        opts = ctx['pending_options']
        if 1 <= num <= len(opts):
            sel = opts[num-1]
            resp, nom, det = resumir_opiniones_local(sel, df, llm)
            nombre = nom or sel
            cards = obtener_restaurant_cards([nombre], df, llm)
            return resp, "resumen", None, obtener_coordenadas([nombre], df), cards, det
        return f"Elegí entre 1 y {len(opts)}", "resumen", opts, [], [], ""

    # Stats
    if any(p in q_low for p in ["cuantos", "cantidad", "total"]):
        # Lógica simplificada de stats
        total = df['restaurante'].nunique()
        return f"Tengo {total} locales registrados.", "estadisticas", None, [], [], ""

    # Resumen directo
    if any(k in q_low for k in ["opiniones", "info", "que onda"]):
        for prep in [" de ", " sobre "]:
            if prep in q_low:
                target = q_low.split(prep, 1)[1].strip()
                resp, nom, det = resumir_opiniones_local(target, df, llm)
                if nom:
                    cards = obtener_restaurant_cards([nom], df, llm)
                    return resp, "resumen", None, obtener_coordenadas([nom], df), cards, det

    # RAG Default
    docs = vectorstore.similarity_search(query, k=15)
    locales = list(set([d.metadata.get('nombre') for d in docs]))[:5]
    
    rag_resp = "Acá tenés las mejores opciones que encontré según las reseñas." 
    
    cards = obtener_restaurant_cards(locales, df, llm)
    locs = obtener_coordenadas(locales, df)
    
    return rag_resp, "rag", None, locs, cards, ""

# --- ENDPOINTS ---
@app.post("/chat", response_model=QueryResponse)
def chat(req: QueryRequest):
    try:
        resp, mode, pend, locs, cards, det = procesar_consulta(
            req.query, df, vectorstore, llm, req.conversation_context
        )
        new_ctx = req.conversation_context.copy()
        if pend: new_ctx['pending_options'] = pend
        elif 'pending_options' in new_ctx: del new_ctx['pending_options']
        
        return QueryResponse(
            response=resp, mode=mode, conversation_context=new_ctx,
            locations=locs, restaurant_cards=cards, detail_content=det
        )
    except Exception as e:
        print(f"Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
