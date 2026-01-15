import os
import json
import re
import unicodedata
import asyncio
import logging
import math
import time # Added for debugging
import urllib.request
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from zoneinfo import ZoneInfo
import pandas as pd
import numpy as np
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_postgres import PGVector
from sqlalchemy import create_engine
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from upstash_redis import Redis
from rapidfuzz import process, fuzz





# ==========================================
# 0. CONFIGURACIÓN DE LOGS
# ==========================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("QueMorfamos")

# ==========================================
# 1. CONFIGURACIÓN DE ENTORNO
# ==========================================
load_dotenv("mis_claves.env")

DATABASE_URL = os.getenv("DATABASE_URL")
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "reviews_embeddings")
UPSTASH_REDIS_REST_URL = os.getenv("UPSTASH_REDIS_REST_URL")
UPSTASH_REDIS_REST_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
THEIPAPI_KEY = os.getenv("THEIPAPI_KEY")

logger.info(f"🔌 Iniciando BACKEND con Colección: '{COLLECTION_NAME}'")

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
    
    # Notificar activación del backend
    try:
        await _send_discord_webhook("🟢 **Backend ACTIVADO** - Servidor iniciado en Fly.io")
    except Exception as e:
        logger.warning(f"No se pudo notificar startup a Discord: {e}")
    
    if DATABASE_URL:
        try:
            engine = create_engine(DATABASE_URL)
            
            # Cargar reviews con datos del lugar (JOIN)
            query = """
                SELECT 
                    r.restaurante, r.autor, r.rating_user, r.texto, 
                    r.fecha_aproximada as fecha, r.review_id,
                    l.rating_gral, l.total_reviews_google, l.direccion,
                    l.latitud, l.longitud, l.barrio, l.zona, l.categoria
                FROM reviews r
                LEFT JOIN lugares l ON r.restaurante = l.nombre
            """
            df = pd.read_sql(query, engine)
            logger.info(f"📊 Datos cargados desde PostgreSQL: {len(df)} filas")
            
            cols = ['restaurante', 'texto', 'direccion', 'barrio', 'zona', 'autor', 'fecha']
            for col in cols:
                if col in df.columns:
                    df.loc[:, col] = df[col].fillna("").astype(str).str.strip()
            
            # --- FILTRO TEMPORAL: Solo Neuquén Capital (Q8300/1/2) ---
            # El usuario pidió excluir Cipolletti/San Martín.
            mask_neuquen = df['direccion'].str.contains('Q8300|Q8301|Q8302', case=False, na=False)
            df = df[mask_neuquen]
            logger.info(f"📊 Datos filtrados (Solo Neuquén): {len(df)} filas")
            
            # Convertir rating_gral (puede venir como "4,5")
            if 'rating_gral' in df.columns:
                df.loc[:, 'rating_gral'] = df['rating_gral'].astype(str).str.replace(',', '.', regex=False)
                df.loc[:, 'rating_gral'] = pd.to_numeric(df['rating_gral'], errors='coerce').fillna(0.0)
            else:
                df['rating_gral'] = 0.0
            
            def _norm(s):
                if pd.isna(s) or s is None: return ""
                t = str(s).lower().strip()
                t = unicodedata.normalize('NFD', t)
                t = ''.join(ch for ch in t if unicodedata.category(ch) != 'Mn')
                t = re.sub(r'[^\w\s]', '', t)
                return t
            df.loc[:, 'restaurante_ascii'] = df['restaurante'].apply(_norm)
            df.loc[:, 'texto_ascii'] = df['texto'].apply(_norm)
            logger.info(f"✅ DataFrame cargado: {len(df)} filas.")
        except Exception as e:
            logger.error(f"❌ Error cargando datos: {e}")
            df = pd.DataFrame()
    else:
        df = pd.DataFrame()

    try:
        embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
        vectorstore = PGVector(
            connection=DATABASE_URL,
            embeddings=embeddings,
            collection_name=COLLECTION_NAME,
            use_jsonb=True
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
    
    # Notificar desactivación del backend
    logger.info("🛑 Apagando servidor...")
    try:
        await _send_discord_webhook("🔴 **Backend DETENIDO** - Servidor apagado por inactividad (Fly.io)")
    except Exception as e:
        logger.warning(f"No se pudo notificar shutdown a Discord: {e}")

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




from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse, StreamingResponse
from langchain_core.messages import BaseMessage

# ==========================================
# HELPER: STREAM BUFFER
# ==========================================
async def astream_buffer(llm, prompt, cache_key=None, cache_instance=None):
    """
    Yields tokens from LLM stream and buffers the full response.
    Returns the full text at the end via a special event or just side-effect?
    Actually, we just yield the tokens. The caller accumulates.
    But to handle caching, we can do it here if provided.
    """
    buffer = ""
    async for chunk in llm.astream(prompt):
        if isinstance(chunk, str):
            token = chunk
        elif isinstance(chunk, BaseMessage):
            token = chunk.content
        else:
            token = str(chunk)
            
        buffer += token
        yield token

    if cache_key and cache_instance:
        cache_instance.set_json("resumen_texto", cache_key, buffer)


class DoubleSlashMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        path = request.url.path
        if path.startswith("//"):
            # Si viene con // lo corregimos internamente
            new_path = path.replace("//", "/", 1)
            scope = request.scope
            scope["path"] = new_path
        return await call_next(request)

app.add_middleware(DoubleSlashMiddleware)

class RestaurantCard(BaseModel):
    nombre: str
    rating: float = 0
    total_reviews: int = 0
    direccion: str = ""
    barrio: str = ""
    zona: str = ""
    categoria: str = ""
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
    categoria: str = ""
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

def _truncate_text(text: str, max_len: int) -> str:
    text = safe_str(text)
    return (text[: max_len - 1] + "…") if len(text) > max_len else text

def extract_client_ip(request: Request) -> str:
    try:
        headers = request.headers
        for key in ("cf-connecting-ip", "true-client-ip", "x-real-ip", "fly-client-ip", "x-forwarded-for"):
            val = headers.get(key)
            if not val:
                continue
            if key == "x-forwarded-for":
                ip = val.split(",")[0].strip()
            else:
                ip = val.strip()
            if ip:
                return ip
        if request.client and request.client.host:
            return request.client.host
    except Exception:
        pass
    return "unknown"

def parse_user_agent(user_agent: str) -> dict:
    """Extrae info del User-Agent sin librerías externas"""
    ua = user_agent.lower() if user_agent else ""
    
    # Detectar dispositivo
    if any(x in ua for x in ['mobile', 'android', 'iphone', 'ipod', 'blackberry', 'windows phone']):
        device = "📱 Mobile"
    elif 'tablet' in ua or 'ipad' in ua:
        device = "📱 Tablet"
    else:
        device = "💻 Desktop"
    
    # Detectar navegador
    if 'edg' in ua:
        browser = "Edge"
    elif 'chrome' in ua and 'chromium' not in ua:
        browser = "Chrome"
    elif 'firefox' in ua:
        browser = "Firefox"
    elif 'safari' in ua and 'chrome' not in ua:
        browser = "Safari"
    elif 'opera' in ua or 'opr' in ua:
        browser = "Opera"
    else:
        browser = "Otro"
    
    # Detectar SO
    if 'windows' in ua:
        os_name = "Windows"
    elif 'mac os' in ua or 'macos' in ua:
        os_name = "macOS"
    elif 'android' in ua:
        os_name = "Android"
    elif 'iphone' in ua or 'ipad' in ua or 'ipod' in ua:
        os_name = "iOS"
    elif 'linux' in ua:
        os_name = "Linux"
    else:
        os_name = "Desconocido"
    
    return {
        "device": device,
        "browser": browser,
        "os": os_name,
        "raw": user_agent[:100] if user_agent else "Unknown"
    }

async def get_ip_location(ip: str) -> dict:
    """Obtiene país y ciudad desde IP usando theipapi.com"""
    # Detectar IPs locales/privadas (IPv4 e IPv6)
    if not ip or ip == "unknown":
        logger.info(f"🏠 IP desconocida, usando ubicación por defecto")
        return {"country": "Desconocido", "city": "", "flag": "❓"}
    
    # IPs privadas IPv4
    if ip.startswith(("127.", "192.168.", "10.", "172.16.", "172.17.", "172.18.", "172.19.", 
                       "172.20.", "172.21.", "172.22.", "172.23.", "172.24.", "172.25.", 
                       "172.26.", "172.27.", "172.28.", "172.29.", "172.30.", "172.31.")):
        logger.info(f"🏠 IP privada IPv4 detectada: {ip}")
        return {"country": "Local/LAN", "city": "", "flag": "🏠"}
    
    # IPs privadas/locales IPv6
    if ip.startswith(("::1", "fc00:", "fd00:", "fe80:")):
        logger.info(f"🏠 IP privada IPv6 detectada: {ip}")
        return {"country": "Local/LAN", "city": "", "flag": "🏠"}
    
    if not THEIPAPI_KEY:
        logger.warning("⚠️ THEIPAPI_KEY no configurada, usando ubicación por defecto")
        return {"country": "Desconocido", "city": "", "flag": "🌍"}
    
    try:
        logger.info(f"🌍 Consultando geolocalización para IP: {ip}")
        
        def _fetch_sync():
            # Fix V2: URL correcta validada con Curl
            url = f"https://theipapi.com/v1/ip/{ip}?api_key={THEIPAPI_KEY}"
            logger.info(f"📡 Consultando theipapi.com")
            # Headers mínimos necesarios, a veces User-Agent genérico ayuda
            req = urllib.request.Request(url, headers={"User-Agent": "curl/7.68.0"}) 
            with urllib.request.urlopen(req, timeout=5) as resp:
                raw = resp.read().decode('utf-8')
                logger.info(f"📥 Respuesta API: {raw[:200]}...")  # Log truncado
                return json.loads(raw)
        
        data = await asyncio.to_thread(_fetch_sync)
        
        # theipapi.com devuelve datos en body.location
        if data.get('status') == 'OK' and 'body' in data:
            body = data['body']
            location = body.get('location', {})
            
            country = location.get('country', 'Desconocido')
            city = location.get('city', '')
            code = location.get('country_code', '')
            
            # Emojis de banderas (offset desde 🇦 = U+1F1E6)
            flag = "🌍"
            if code and len(code) == 2:
                try:
                    flag = "".join(chr(0x1F1E6 + ord(c) - ord('A')) for c in code.upper())
                except:
                    flag = "🌍"
            
            logger.info(f"✅ Geolocalización exitosa: {flag} {country} ({city})")
            return {"country": country, "city": city, "flag": flag}
        else:
            logger.warning(f"⚠️ API theipapi.com respuesta inesperada: {data.get('status')}")
            
    except Exception as e:
        logger.error(f"❌ Error obteniendo geolocalización de IP {ip}: {e}", exc_info=True)
    
    return {"country": "Desconocido", "city": "", "flag": "🌍"}

async def _send_discord_webhook(content: str) -> None:
    if not DISCORD_WEBHOOK_URL:
        return

    payload = {"content": content}

    def _post_sync():
        import urllib.request

        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            DISCORD_WEBHOOK_URL,
            data=data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "QueMorfamosBot/1.0",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            resp.read()

    try:
        await asyncio.to_thread(_post_sync)
    except Exception as e:
        logger.warning(f"⚠️ No se pudo enviar log a Discord: {e}")

async def log_user_query_to_discord(
    request: Request, 
    query: str, 
    tone: str = None,
    response_time: float = None,
    mode: str = None,
    restaurants: list = None,
    keywords: list = None,
    used_cache: bool = False,
    ai_provider: str = None,
    context_info: dict = None,
    strikes: int = 0,
    zona_detectada: str = None
) -> None:
    try:
        dt = datetime.now(ZoneInfo("America/Argentina/Buenos_Aires"))
        ts = dt.strftime("%d/%m/%Y %H:%M:%S (ART)")
        ts_iso = dt.isoformat()
    except Exception:
        dt = datetime.now(timezone.utc)
        ts = dt.strftime("%d/%m/%Y %H:%M:%S (UTC)")
        ts_iso = dt.isoformat()
    
    ip = extract_client_ip(request)
    ua_info = parse_user_agent(request.headers.get("user-agent", ""))
    location = await get_ip_location(ip)
    q = _truncate_text(query, 1700).replace("```", "`")
    tone_display = sanitize_tone(tone).title() if tone else "Cordial"
    referer = request.headers.get("referer", "Directo")
    language = request.headers.get("accept-language", "es").split(",")[0]

    # Formato de ubicación
    loc_str = f"{location['flag']} {location['country']}"
    if location['city']:
        loc_str += f" ({location['city']})"

    # Mensaje Discord (compacto, query primero)
    zona_str = zona_detectada.title() if zona_detectada else "Todo Neuquén"
    
    discord_parts = [
        f"💬 **{q}**",  # Query PRIMERO y destacada
        f"🗺️ Zona: {zona_str}",
        f"🕒 {ts}",
        f"📍 {loc_str}",
        f"{ua_info['device']} {ua_info['browser']} ({ua_info['os']})",
        f"🎭 {tone_display}"
    ]
    
    if response_time:
        discord_parts.append(f"⏱️ {response_time:.2f}s")
    
    if mode:
        mode_emoji = {"rag": "🔍", "resumen": "📋", "estadisticas": "📊", "blocked": "🚫"}.get(mode, "💬")
        discord_parts.append(f"{mode_emoji} Modo: {mode}")
    
    if restaurants:
        rest_str = ", ".join(restaurants[:3])
        if len(restaurants) > 3:
            rest_str += f" (+{len(restaurants)-3} más)"
        discord_parts.append(f"🏪 {rest_str}")
    
    content = "\n".join(discord_parts)
    await _send_discord_webhook(content)
    
    # Log a archivo JSON (TODA la info detallada)
    log_entry = {
        "timestamp": ts_iso,
        "ip": ip,
        "country": location['country'],
        "city": location['city'],
        "device": ua_info['device'].replace("📱 ", "").replace("💻 ", ""),
        "browser": ua_info['browser'],
        "os": ua_info['os'],
        "language": language,
        "referer": referer,
        "tone": tone_display,
        "query": query,
        "response_time_seconds": response_time,
        "mode": mode,
        "restaurants_returned": restaurants or [],
        "keywords_detected": keywords or [],
        "used_cache": used_cache,
        "ai_provider": ai_provider,
        "has_context": bool(context_info),
        "last_entity": context_info.get('last_entity') if context_info else None,
        "strikes": strikes,
        "user_agent": ua_info['raw']
    }
    
    try:
        with open("user_queries.log", "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning(f"⚠️ No se pudo guardar log a archivo: {e}")

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
        "- NO saludes ('Hola', 'Bienvenido', etc.). Andá directo al grano.\n"
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
    df_local.loc[:, 'orden_fecha'] = df_local['fecha'].apply(fecha_a_orden)
    if 'rating_user' in df_local.columns:
        df_local.loc[:, 'rating_user'] = pd.to_numeric(df_local['rating_user'], errors='coerce').fillna(0).astype(int)
    else:
        df_local.loc[:, 'rating_user'] = 0

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
            categoria=safe_str(item['row'].get('categoria')),
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
                total_reviews=safe_int(row.get('total_reviews_google')),
                categoria=safe_str(row.get('categoria'))
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

    cache_key = f"analysis_v86_{q_lower.strip()}"
    cached = cache.get_json("analysis", cache_key)
    if cached: return cached

    template = f"""
    Analiza la intención del usuario. Query: "{query}"
    
    1. SEGURIDAD:
       - Si es sexual/insulto -> "BLOCK".
       - Excepciones: Comida/Infantil es seguro.
    
    2. CLASIFICACIÓN: "PRODUCTO" (Pizza) o "VIBE" (Pelotero, Romántico).
    
    3. EXTRAER KEYWORDS Y SINÓNIMOS (CRÍTICO):
       - "keywords": La palabra exacta buscada (singular).
       - "synonyms": Lista de 3 o 4 palabras relacionadas.
       
    4. UBICACIÓN GEOGRÁFICA (MUY IMPORTANTE):
       - "donde": Extraer la zona/barrio si el usuario lo menciona.
       - Patrones comunes: "en el X", "zona X", "cerca del X", "del X".
       
       EJEMPLOS CRÍTICOS:
       - "bares en el rio" -> donde: "rio"
       - "pizzerias en el centro" -> donde: "centro"
       - "hamburgueserias en el alto" -> donde: "alto"
       - "bares zona oeste" -> donde: "oeste"
       - "lugares en la costa" -> donde: "rio"
       - "mejores pizzas" -> donde: null (sin ubicación)
    
    Responde SOLO JSON válido:
    {{
        "tipo": "VIBE", 
        "keywords": ["bar"], 
        "synonyms": ["cerveceria", "pub"],
        "donde": "rio"
    }}
    
    Si NO hay ubicación, "donde" debe ser null.
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
    
    # HEURISTIC SAFEGUARD: Si la query pide recomendación explícita, NO buscamos match exacto/parcial.
    # Esto previene que "mejores bares en el oeste" matchee con el lugar "Oeste".
    rec_keywords = ["mejores", "mejor", "top", "rank", "ranking", "recomendame", "recomenda", 
                   "busco", "lugar para", "lugares para", "donde comer", "donde cenar", "donde ir", 
                   "lugares con", "lugares de", "bares en", "restaurantes en", "opciones", "opcion", "algo con",
                   # Patrones de zona que indican recomendación
                   "en el oeste", "en el centro", "en el alto", "en el norte", "en el sur",
                   "cerca del rio", "cerca del río", "cerca del paseo", "en la costa"]
    
    if any(kw in q_norm for kw in rec_keywords):
        print(f"[DEBUG] 🛡️ Heuristic blocked Exact Match: Detected recommendation intent in '{query}'", flush=True)
        return None
    
    venue_prefixes = {
        "restaurante", "parrilla", "bar", "confiteria", "pizzeria", "bodegon", 
        "cerveceria", "hamburgueseria", "heladeria", "cafe", "bistro", "resto", 
        "rotiseria", "panaderia", "sushi", "casa", "local", "negocio"
    }
    
    stopwords = {"el", "la", "los", "las", "de", "del", "lo", "al", "y", "en", "que", "qué", "tal", "como", "es", "onda", "son",
                 "para", "con", "donde", "por", "sin", "sobre"}
    
    # AQUI ESTÁ LA CLAVE: Agregamos helado/heladeria/postres para que no matcheen nombres parciales
    generic_blocklist = {
        "sushi", "pizza", "pizzas", "burger", "hamburguesa", "hamburguesas", 
        "helado", "helados", "heladeria", "heladerias", "crema", "cremas", 
        "birra", "cerveza", "cervezas", "birra", "birras", "cafe", "café", "parrilla", "pasta", 
        "pastas", "milanesa", "milanesas", "ensalada", "comida", "postre", 
        "postres", "resto", "bar", "almuerzo", "cena", "menu",
        "para", "con", "donde", # Safety extra
        "tacc", "celiaco", "celiacos", "vegano", "vegana", "vegetariano"
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
    - Preguntas sobre un lugar específico por su nombre (PRIMERA VEZ).
    - Ejemplos: "Que opinas de Saluzzo?", "Que tal es Growler?", "Donde queda Rancho Grande?".
    
    PRIORIDAD 3.5: FOLLOWUP (Seguimiento)
    - Preguntas específicas sobre el lugar del que YA estamos hablando.
    - Ejemplos: "Y los precios?", "Como es el ambiente?", "Tienen opciones veganas?", "Donde queda exactamente?".
    - CLAVE: El usuario asume que ya sabes de qué lugar habla.
    
    PRIORIDAD 4: RECOMMENDATION (Búsqueda)
    - Busca opciones para comer o lugares con características, SIN un lugar específico en mente.
    - Ejemplos: "mejores cervecerías", "mejores helados", "Lugares con pelotero", "Quiero sushi", "Parrilla barata".
    
    PRIORIDAD 5: GENERAL (Charla y Otros)
    - Saludos, agradecimientos, incoherencias o temas off-topic.
    
    Responde SOLO la palabra de la categoría (ej: BLOCK).
    """
    
    try:
        # Usamos last_entity en el prompt si existe, ayuda al contexto
        context_str = f" (Contexto previo: Hablábamos de {last_entity})" if last_entity else ""
        
        res = await llm.ainvoke(system_prompt + f"\nQUERY USUARIO: '{query}'{context_str}")
        intencion = res.content.strip().upper().replace('"', '').replace('.', '')
        
        validos = ["BLOCK", "STATS", "SPECIFIC", "RECOMMENDATION", "GENERAL", "FOLLOWUP"]
        
        for v in validos:
            if v in intencion: return v
            
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
        
        if len(keyword_ascii) < 3:
            return f"No encontré una categoría clara en '{query}'.", []

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

async def resumir_opiniones_local_gen(query_str, df, llm, topic=None, tone='cordial', es_seleccion_directa=False):
    """
    Generator version of resumir_opiniones_local.
    Yields:
      - {"type": "token", "content": "..."}
      - {"type": "meta", "restaurante": "...", "found": True}
      - {"type": "menu", "text": "...", "options": [...]}
      - {"type": "error", "text": "..."}
    """
    # 1. Validación básica
    if not query_str: 
        yield {"type": "error", "text": "Nombre vacío."}
        return
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
                yield {
                    "type": "menu", 
                    "text": f"Encontré varios lugares con ese nombre. ¿Cuál decís?\n\n{lista_txt}\n\n*(Escribí el número)*", 
                    "options": encontrados
                }
                return

    # 3. Si no encontró nada
    if not encontrados: 
        yield {"type": "error", "text": f"No tengo info de **{query_str}**. Probá con otro nombre."}
        return
    
    # 4. Chequeo de Caché
    restaurante = encontrados[0]
    yield {"type": "meta", "restaurante": restaurante, "found": True}

    cache_key = f"{restaurante}_{topic}_{sanitize_tone(tone)}" if topic else f"{restaurante}__{sanitize_tone(tone)}"
    cached_text = cache.get_json("resumen_texto", cache_key)
    if cached_text: 
        msg = f"Acá te paso la data de **{restaurante}**:"
        yield {"type": "token", "content": msg}
        # Yield the rest as one big token or separate? One is fine.
        # But wait, we want to simulate stream? No, if cached just return fast.
        # However, for the endpoint consuming this, it expects tokens.
        yield {"type": "token", "content": "\n\n" + cached_text}
        return

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
        chain = ChatPromptTemplate.from_template(tpl) | llm | StrOutputParser()
        args = {
            "rest": restaurante, 
            "rat": safe_float(row_data.get('rating_gral')),
            "revs": reviews_txt
        }
        
        # Stream dispatch
        async for token in astream_buffer(chain, args, cache_key=cache_key, cache_instance=cache):
            yield {"type": "token", "content": token}
            
    except Exception as e:
        yield {"type": "error", "text": "No pude generar el resumen."}

async def resumir_opiniones_local(query_str, df, llm, topic=None, tone='cordial', es_seleccion_directa=False):
    """
    Wrapper legacy for compatibility with non-streaming callers.
    """
    full_text = ""
    restaurante = None
    options = None
    
    async for event in resumir_opiniones_local_gen(query_str, df, llm, topic, tone, es_seleccion_directa):
        if event["type"] == "token":
            full_text += event["content"]
        elif event["type"] == "meta":
            restaurante = event["restaurante"]
        elif event["type"] == "menu":
            full_text = event["text"]
            options = event["options"]
        elif event["type"] == "error":
            full_text = event["text"]

    # Original logic returned (text, restaurante, detail, options)
    # Detail is usually same as text
    return full_text, restaurante, full_text, options

async def responder_followup_gen(restaurante, query, df, llm, tone='cordial'):
    """
    Generates a targeted answer to a follow-up question based on reviews.
    Yields tokens.
    """
    # 1. Validation
    if not restaurante:
        yield {"type": "token", "content": "No tengo un lugar seleccionado para responderte."}
        return
        
    mask = df['restaurante'] == restaurante
    if not mask.any():
        yield {"type": "token", "content": f"No encuentro info de {restaurante}."}
        return
    
    # 2. Get reviews and filter by topic/relevance
    # reusing logic from rankear_reviews_por_topico 
    sorted_reviews = rankear_reviews_por_topico(df[mask], query)
    
    # Take top 15 reviews to have enough context
    reviews_txt = "\n".join([f"- {safe_str(r.get('texto'))[:300]}" for _, r in sorted_reviews.head(15).iterrows()])
    
    if not reviews_txt.strip():
        yield {"type": "token", "content": f"No encontré reseñas específicas sobre '{query}' para {restaurante}."}
        return

    # 3. Prompt specific for Q&A
    tone_prefix = tone_system_instruction(tone)
    prompt = f"""{tone_prefix}
    Estás respondiendo una PREGUNTA ESPECÍFICA sobre el restaurante "{restaurante}".
    
    PREGUNTA DEL USUARIO: "{query}"
    
    Tus fuentes (Reseñas reales):
    {reviews_txt}
    
    Instrucciones:
    1. Responde DIRECTAMENTE a la pregunta basándote SOLO en las reseñas.
    2. Si las reseñas dicen algo relevante, sintetizalo.
    3. Si las reseñas NO dicen nada sobre el tema (ej: pregunta precios y nadie menciona precios), di CLARAMENTE: "No encontré comentarios recientes sobre eso en las reseñas".
    4. NO inventes datos. NO des el resumen general (onda/bueno/malo). Solo la respuesta.
    5. Usa formato Markdown. Sé conciso (max 3 parrafos).
    
    Respuesta:
    """
    
    try:
        chain = ChatPromptTemplate.from_template(prompt) | llm | StrOutputParser()
        args = {}
        # Simulate typing/thinking? No just stream.
        async for token in astream_buffer(chain, args):
            yield {"type": "token", "content": token}
    except Exception as e:
        logger.error(f"Error responder_followup: {e}")
        yield {"type": "token", "content": "Tuve un error procesando tu pregunta."}


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
    Eres un JUEZ DE CALIDAD ESTRICTO. Query usuario: "{query}"
    
    Analiza la EVIDENCIA de los locales y decide cuáles aprobar.
    
    REGLAS CRÍTICAS:
    1. APROBAR si la evidencia confirma que el lugar ES DEL TIPO solicitado (bar, pizzería, etc.).
    2. RECHAZAR si dice "NO hay", "NO tiene", "Falta" respecto al PRODUCTO o SERVICIO buscado.
    3. CUIDADO con dietas (vegano, celíaco, sin tacc): Si la reseña dice "No hay opciones veganas", eliminarlo.
    
    ⚠️ IMPORTANTE - IGNORA LA UBICACIÓN:
    - NO evalúes si el lugar está "en el río", "en el centro", etc.
    - La ubicación YA fue filtrada antes. Tu trabajo es SOLO verificar el TIPO de lugar.
    - Ejemplo: "bares en el rio" -> Solo evalúa si ES un bar/cervecería. NO busques evidencia de "río".
    
    4. Si la evidencia es vaga o irrelevante para el TIPO de lugar, ELIMINAR.
    
    CANDIDATOS A ANALIZAR:
    {texto_validacion}
    
    Responde SOLO un JSON válido con esta estructura:
    {{
        "aprobados": ["Local A", "Local B"],
        "rechazados": {{
             "Local C": "Breve razón del rechazo"
        }}
    }}
    """
    
    try:
        res = await llm.ainvoke(prompt)
        clean = res.content.strip().replace("```json", "").replace("```", "")
        data = json.loads(clean)
        
        aprobados = data.get("aprobados", [])
        rechazados = data.get("rechazados", {})
        
        if rechazados:
            print(f"[DEBUG] 🚫 Razones de rechazo del Juez:", flush=True)
            for local, razon in rechazados.items():
                print(f"   - {local}: {razon}", flush=True)
        
        return aprobados
    except Exception as e:
        logger.error(f"Error Juez LLM: {e}")
        # Fallback: Si falla el JSON, devolvemos todo (mejor pecar de exceso que de defecto)
        return candidatos

# ==========================================
# 6. ROUTER PRINCIPAL
# ==========================================


def aplicar_filtro_zona(candidatos, df, zona_buscada):
    """
    Filtra la lista de nombres de restaurantes según si coinciden con la zona buscada.
    Busca en las columnas: 'zona', 'barrio' y 'direccion'.
    """
    if not zona_buscada: return candidatos
    
    z_clean = zona_buscada.lower().strip()
    
    # Mapeo de sinónimos comunes de Neuquén
    search_terms = [z_clean]
    
    # Si busca "rio", "paseo" o "costa", ampliamos la búsqueda
    if any(x in z_clean for x in ["rio", "río", "paseo", "costa", "limay", "isla"]):
        # Agregamos términos clave que suelen aparecer en direcciones o zonas cercanas al río
        search_terms.extend([
            "rio", "río", "limay", "balneario", 
            "paseo", "costa", "costanera", "ribera",
            "isla", "132", "isla 132" 
        ])
        
    if "alto" in z_clean or "norte" in z_clean:
         search_terms.extend(["alto", "norte", "barda", "parque industrial", "terrazas"])
         
    if "centro" in z_clean:
         search_terms.extend(["centro", "bajo"]) # Centro y Bajo a veces se solapan
    
    if "oeste" in z_clean:
         search_terms.extend(["oeste", "aeropuerto", "canal"])

    candidatos_filtrados = []
    
    for local in candidatos:
        mask = df['restaurante'] == local
        if not mask.any(): continue
        
        row = df[mask].iloc[0]
        
        # Juntamos toda la info geográfica del local en un solo string
        geo_data = f"{safe_str(row.get('zona'))} {safe_str(row.get('barrio'))} {safe_str(row.get('direccion'))}".lower()
        
        # Chequeamos si CUALQUIERA de los términos buscados está en la data
        if any(term in geo_data for term in search_terms):
            candidatos_filtrados.append(local)
            
    
    # Si el filtro fue muy agresivo y no quedó nadie, devolvemos VACÍO (Hard Filter)
    # Usuario solicitó explícitamente una zona. Si no hay, mejor decir "no hay" que mentir.
    if not candidatos_filtrados:
        print(f"[DEBUG] ⚠️ Filtro de zona '{zona_buscada}' (terms: {search_terms}) eliminó TODOS los candidatos.", flush=True)
        return []
        
    return candidatos_filtrados
        
    return candidatos_filtrados

async def resolver_target_con_llm(query, last_entity, llm):
    """
    Decide si el usuario sigue hablando del 'last_entity' o cambia a un tema nuesvo.
    Devuelve:
    - nombre de la entidad (last_entity o nueva)
    - "NONE" si no hay entidad clara
    """
    if not last_entity: return "NONE"
    
    prompt = f"""
    Eres un asistente de contexto.
    Contexto previo: Hablábamos de "{last_entity}".
    Input usuario: "{query}"
    
    ¿El usuario sigue preguntando sobre "{last_entity}" o cambió a un lugar/tema nuevo?
    - Si la pregunta es sobre "{last_entity}" (ej: "precio?", "donde queda?", "y opiniones?"), RESPONDE: {last_entity}
    - Si pregunta sobre OTRO lugar (ej: "y Atila?", "que onda Growler?", "Elaskar"), RESPONDE EL NOMBRE DEL NUEVO LUGAR LIMPIO.
    - Si no busca info de un lugar específico, RESPONDE: NONE.
    
    Responde SOLO el nombre o NONE.
    """
    try:
        res = await llm.ainvoke(prompt)
        text = res.content.strip().replace('"', '').replace('.', '')
        # Basic cleanup
        if text.upper() == "NONE": return "NONE"
        return text
    except:
        return last_entity # Conservative fallback

async def procesar_consulta_gen(query, df, vectorstore, llm_mini, llm_smart, ctx=None, user_ip=None):
    """
    Generator that handles the chat logic.
    Yields:
      - {"type": "token", "content": "..."}
      - {"type": "meta", "mode": "...", "cards": [...], "locs": [...], "pending": ..., "intent": "..."}
      - {"type": "debug", "message": "..."}
      - {"type": "error", "message": "..."}
    """
    if ctx is None: ctx = {}
    t_start = time.time()
    
    # 🐛 DEBUG DE ENTRADA
    yield {"type": "debug", "message": f"🟢 ENTRADA A CEREBRO | Query: '{query}'"}

    tone = sanitize_tone(ctx.get('tone'))
    
    # ==========================================
    # 1. CAPA DE SEGURIDAD (NUCLEAR)
    # ==========================================
    if user_ip and cache.get_value(f"ban:{user_ip}"): 
        yield {"type": "meta", "mode": "blocked"}
        yield {"type": "token", "content": "⛔ Sistema bloqueado."}
        return
    
    strikes = ctx.get('strikes', 0)
    if strikes >= 5: 
        yield {"type": "meta", "mode": "blocked"}
        yield {"type": "token", "content": "⛔ Bloqueado."}
        return

    if check_keyword_ban(query):
        ctx['strikes'] = strikes + 1
        yield {"type": "meta", "mode": "rag"} # Or 'blocked'? Logic said 'rag' before.
        yield {"type": "token", "content": f"Epa, esa búsqueda no va. ({strikes+1}/5)"}
        return

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
            
            # Call generator
            # Need to capture nombre_real from meta event
            nombre_real = None
            async for event in resumir_opiniones_local_gen(seleccion, df, llm_mini, original_topic, tone, True):
                if event["type"] == "meta":
                    if "restaurante" in event:
                         nombre_real = event["restaurante"]
                    # Propagate meta if relevant? internal meta might not match outer meta structure.
                elif event["type"] == "token":
                    yield event
                elif event["type"] == "error":
                     yield {"type": "token", "content": event["text"]}
            
            if nombre_real:
                cards = await obtener_restaurant_cards([nombre_real], df, llm_mini, original_topic, tone)
                locs = obtener_coordenadas([nombre_real], df)
                # Yield meta at the end or when available
                yield {"type": "meta", "mode": "resumen", "cards": cards, "locs": locs}
            return
            
        yield {"type": "meta", "mode": "resumen", "pending": pending}
        yield {"type": "token", "content": f"Elegí entre 1 y {len(opciones)}"}
        return

    # ==========================================
    # 3. SMART ROUTING (EL CEREBRO V8)
    # ==========================================
    last_ent = ctx.get('last_entity')
    
    # OVERRIDE: Si detectamos mención explícita, forzamos SPECIFIC_INFO
    # Esto evita que el router se confunda con preguntas cortas como "Y Atila?" -> GENERAL
    forced_entity = detectar_mencion_exacta(query, df)
    
    # IMPROVED: Deep Fuzzy Check using RapidFuzz
    # This catches "Vikingos" -> "VIKINGS PIZZERIA" even if strict substring fails.
    if not forced_entity:
        # HEURISTIC: Skip fuzzy override if query looks like a recommendation request
        recommendation_keywords = [
            "mejores", "mejor", "top", "rank", "ranking", "recomendame", "recomenda", "busco", 
            "lugar para", "donde comer", "donde cenar", "donde ir", 
            "lugares en", "bares en", "restaurantes en",
            # Patrones de zona (el Zone Safety Check cubre el resto)
            "en el oeste", "en el centro", "en el alto", "en el norte", "en el sur",
            "en la costa", "cerca del rio", "cerca del río", "cerca del paseo",
            "en el", "en la", "cerca de", "zona de"
        ]
        is_recommendation = any(kw in query.lower() for kw in recommendation_keywords)

        # Extra Check: Regex for "Category en/cerca de Zone" pattern
        # This catches "bares cerca del rio" even if "cerca del" isn't in keywords exactly
        import re
        category_location_pattern = re.search(r"\b(en|cerca)\b\s+(el|la|los|las|de|del)?\s*\b(alto|centro|rio|río|costa|paseo|oeste|sur|norte)\b", query.lower())
        if category_location_pattern:
            is_recommendation = True
            print(f"[DEBUG] 🛡️ Regex Protection: Detected Location Pattern '{category_location_pattern.group()}' -> Forcing RECOMMENDATION", flush=True)

        if not is_recommendation:
            candidates = df['restaurante'].unique().tolist()
            # Use token_set_ratio to handle "Vikingos" vs "VIKINGS PIZZERIA" (Partial overlap is good)
            # score_cutoff=85 allows some typo tolerance.
            match = process.extractOne(query, candidates, scorer=fuzz.token_set_ratio, score_cutoff=85)
            
            # Extra safety: If match is found, check token_sort_ratio too (stricter) 
            # or ensure length similarity to avoid "Oeste" matching "mejores bares en el oeste"
            if match:
                candidate_name = match[0]
                score = match[1]
                
                # Length Safety Check: If candidate name is very short compared to query, it might be a false positive substring
                # e.g. Query: "mejores bares en el oeste" (25 chars) vs Candidate: "Oeste" (5 chars) -> Ratio ~0.2
                # But valid: "mcdonalds" (9) vs "McDonald's" (10) -> Ratio ~0.9
                len_ratio = len(candidate_name) / len(query)
                
                # NEW: Zone Word Safety Check
                # Si el candidato contiene una palabra de zona Y la query también la menciona,
                # probablemente es un falso positivo (ej: "cervecerias en el oeste" -> "Aripo Oeste")
                zone_words = {"oeste", "centro", "norte", "sur", "alto", "costa", "rio", "río", "paseo", "este"}
                candidate_lower = candidate_name.lower()
                query_lower = query.lower()
                
                zone_match_rejected = False
                for zone in zone_words:
                    # Si la zona aparece en el candidato Y en la query
                    if zone in candidate_lower and zone in query_lower:
                        # Y el candidato NO es SOLO la zona (tiene más palabras)
                        # Ej: "Aripo Oeste" contiene "oeste" y query contiene "oeste" -> rechazar
                        # Pero "Oeste Bar" donde query es "oeste" -> podría ser válido
                        if len(candidate_name.split()) > 1:  # Tiene múltiples palabras
                            zone_match_rejected = True
                            print(f"[DEBUG] ⚠️ Zone Safety: '{candidate_name}' rejected because it contains zone word '{zone}' that's also in query", flush=True)
                            break
                
                if zone_match_rejected:
                    pass  # No asignar forced_entity
                elif len_ratio > 0.4 or score > 95: # Allow low ratio ONLY if score is near perfect (e.g. exact match inside text)
                     forced_entity = candidate_name
                     print(f"[DEBUG] 🕵️ Fuzzy Override: '{query}' -> '{forced_entity}' (Score: {score}, LenRatio: {len_ratio:.2f})", flush=True)
                else:
                     print(f"[DEBUG] ⚠️ Fuzzy Match rejected by heuristic: '{query}' -> '{candidate_name}' (Score: {score}, LenRatio: {len_ratio:.2f})", flush=True)
        else:
             print(f"[DEBUG] 🛡️ Heuristic blocked Fuzzy Override: Detected recommendation intent in '{query}'", flush=True)

    if forced_entity:
        intencion = "SPECIFIC_INFO"
        print(f"[DEBUG] 🚀 Router Override: Detectado '{forced_entity}' -> Forzando modo SPECIFIC_INFO", flush=True)
    elif is_recommendation:
        intencion = "RECOMMENDATION"
        print(f"[DEBUG] 🧪 Heuristic Force: Recommendation keywords found -> Forzando modo RECOMMENDATION", flush=True)
    else:
        # Use llm_mini for faster routing (Routing doesn't need GPT-4o typically)
        intencion = await clasificar_intencion(query, llm_mini, last_entity=last_ent)
    
    # Ajuste de compatibilidad
    if intencion == "SPECIFIC": intencion = "SPECIFIC_INFO"
    
    yield {"type": "debug", "message": f"🧠 INTENCIÓN: {intencion}"}

    # --- CAMINO A: ESTADÍSTICAS ---
    if intencion == "STATS":
        # SAFETY NET: If we have a last_entity, avoid entering STATS for ambiguous follow-ups
        # like "precios?", "horarios?", "y la carta?".
        # Only allow STATS if the user explicitly asks for magnitude/quantity.
        is_explicit_stats = any(x in query.lower() for x in ["total", "cuantos", "cuántos", "cantidad", "numero de", "número de", "hay "])
        
        if ctx.get('last_entity') and not is_explicit_stats:
             print(f"[DEBUG] 🔄 Re-routing ambiguous STATS '{query}' to SPECIFIC because active context exists", flush=True)
             intencion = "SPECIFIC_INFO"
        else:
             if 'last_entity' in ctx: del ctx['last_entity']
             
             resp, locales = await consultar_estadisticas(query, df, llm_mini)
             cards = obtener_restaurant_cards_simple(locales, df)
        locs = obtener_coordenadas(locales, df)
        
        yield {"type": "meta", "mode": "estadisticas", "cards": cards, "locs": locs}
        yield {"type": "token", "content": resp}
        return

    # --- CAMINO AB: FOLLOWUP (PREGUNTA ESPECÍFICA DE SEGUIMIENTO) ---
    if intencion == "FOLLOWUP":
        target = ctx.get('last_entity')
        
        if not target:
             # Fallback: Router says yes, but we have no context.
             yield {"type": "token", "content": "Perdón, me perdí. ¿De qué lugar estábamos hablando?"}
             yield {"type": "debug", "message": "⚠️ Intent FOLLOWUP sin last_entity"}
        else:
             # Streaming answer
             yield {"type": "debug", "message": f"🔄 FOLLOWUP sobre '{target}'"}
             # Optional: Yield meta to keep UI context? 
             # yield {"type": "meta", "mode": "followup", "restaurante": target} 
             
             async for event in responder_followup_gen(target, query, df, llm_mini, tone):
                 yield event
        return

    # --- CAMINO B: INFO ESPECÍFICA (UN LUGAR) ---
    if intencion == "SPECIFIC_INFO":
        # SAFE INIT
        match_exists = False
        temp_error_msg = None
        options_found = None
        found_valid_content = False
        target = None
        
        # BUG FIX: Prioritize detecting NEW entity in the query over the previous one.
        # Check if query mentions a known place
        nuevo_candidato = detectar_mencion_exacta(query, df)
        
        target = None
        
        if nuevo_candidato:
            # User mentioned a specific place explicitly
            target = nuevo_candidato
            ctx['last_entity'] = target 
        else:
            # AGGRESSIVE SEARCH via RapidFuzz (Best in Class)
            # Replaces manual loop. Matches "Vikingos" to "VIKINGS PIZZERIA".
            candidates = df['restaurante'].unique().tolist()
            match = process.extractOne(query, candidates, scorer=fuzz.token_set_ratio, score_cutoff=85)
            
            match_fuzzy = match[0] if match else None
            
            if match_fuzzy:
                target = match_fuzzy
                ctx['last_entity'] = target
            elif ctx.get('last_entity'):
                # Consult LLM to determine if we act on last_entity or new target
                # This handles "Y el precio?" (Keep) vs "Que onda Elaskar?" (Switch) robustly.
                resolved_target = await resolver_target_con_llm(query, ctx.get('last_entity'), llm_mini)
                
                if resolved_target == "NONE" or not resolved_target:
                     target = query # Treat as generic specific query
                else:
                     target = resolved_target
                     if target != ctx.get('last_entity'):
                         # Detected context switch to new (possibly unknown) entity
                         ctx['last_entity'] = target
            else:
                 target = query

        # Resolve target normalization (redundant if nuevo_candidato found, but safe)
        if target:
            nombre_candidato = detectar_mencion_exacta(target, df)
            if nombre_candidato: target = nombre_candidato
            else:
                # Last resort fuzzy check if target comes from query string
                mask = df['restaurante'].str.lower().str.contains(target.lower().strip(), na=False, regex=False)
                if mask.any(): pass 
                else: 
                     # If target was just 'query' and didn't match anything...
                     # and we have NO last_entity, we might be lost.
                     # But if we had last_entity and logic dropped here?
                     pass

        if target:
            match_exists = True # Assume true if we resolved it or came from context
            
            if match_exists:
                es_solo_navegacion = len(query.strip()) <= len(target.strip()) + 5
                topic_actual = None if es_solo_navegacion else query
                
                if 'original_query' in ctx: del ctx['original_query']
                if topic_actual: ctx['original_query'] = topic_actual

                # Generator Call
                found_valid_content = False
                temp_error_msg = None
                options_found = None
                
                async for event in resumir_opiniones_local_gen(target, df, llm_mini, topic=topic_actual, tone=tone):
                    if event["type"] == "token":
                        yield event
                        found_valid_content = True
                    elif event["type"] == "meta":
                        if "restaurante" in event: nombre_final = event["restaurante"]
                        # If found, propagate meta
                        # yield event # Or wait? stream reader handles meta.
                        # Usually we yield meta at end or inline.
                        # resumir_opiniones_local_gen yields token content.
                        # We should yield meta too for context update? 
                        # The original code only captured nombre_final and yielded meta later?
                        # No, detecting loop logic:
                        # Original:
                        # elif event["type"] == "meta": if "restaurante": nombre_final...
                        # It did NOT yield the meta event?
                        # Wait, let's look at `resumir_opiniones_local_gen` output. It yields meta.
                        pass
                    elif event["type"] == "menu":
                        yield {"type": "token", "content": event["text"]}
                        options_found = event["options"]
                        found_valid_content = True
                    elif event["type"] == "error":
                         temp_error_msg = event["text"]

                if options_found:
                    yield {"type": "meta", "mode": "resumen", "pending": {"options": options_found}}
                    return
                
                if found_valid_content:
                    if nombre_final:
                        ctx['last_entity'] = nombre_final
                        cards = await obtener_restaurant_cards([nombre_final], df, llm_mini, query_context=topic_actual, tone=tone)
                        locs = obtener_coordenadas([nombre_final], df)
                        yield {"type": "meta", "mode": "resumen", "cards": cards, "locs": locs}
                    return
        
        if temp_error_msg:
             # User Request: If not found, STOP. Do not recommend others.
             # Clean up the message slightly if it's the default one
             final_msg = "No tengo información sobre ese lugar específico."
             yield {"type": "token", "content": final_msg}
             return
        
        # Fallback to RECOMMENDATION only if no error but also no content? 
        # (This case shouldn't happen much if 'target' was set)
        intencion = "RECOMMENDATION"

    # --- CAMINO C: RECOMENDACIÓN (PIPELINE ESTRICTO) ---
    if intencion == "RECOMMENDATION":
        vars_to_kill = ['last_entity', 'original_query', 'pending_options']
        for var in vars_to_kill:
            if var in ctx: del ctx[var]

        try:
            analisis = await analizar_query_semantica(query, llm_smart)
            if analisis.get("tipo") == "BLOCK":
                ctx['strikes'] = strikes + 1
                yield {"type": "meta", "mode": "rag"}
                yield {"type": "token", "content": f"Epa, esa búsqueda no va. ({strikes+1}/5)"}
                return

            keywords = analisis.get("keywords", [])
            synonyms = analisis.get("synonyms", [])
            zona_detectada = analisis.get("donde")
            print(f"[DEBUG] 📍 Zona detectada por LLM: '{zona_detectada}'", flush=True)
            
            # FALLBACK: Detección heurística de zona si LLM no la detectó
            if not zona_detectada:
                zona_patterns = [
                    (r'\ben\s+(?:el|la)?\s*(rio|río)', 'rio'),
                    (r'\ben\s+(?:el|la)?\s*(centro)', 'centro'),
                    (r'\ben\s+(?:el|la)?\s*(alto)', 'alto'),
                    (r'\ben\s+(?:el|la)?\s*(oeste)', 'oeste'),
                    (r'\ben\s+(?:el|la)?\s*(este)', 'este'),
                    (r'\b(paseo\s*(?:de\s*la)?\s*costa)', 'rio'),
                    (r'\b(costanera|ribera)', 'rio'),
                    (r'\bzona\s+(rio|río|centro|alto|oeste|este)', None),  # Captura directa
                ]
                q_lower = query.lower()
                for pattern, default_zone in zona_patterns:
                    match = re.search(pattern, q_lower)
                    if match:
                        zona_detectada = default_zone if default_zone else match.group(1)
                        print(f"[DEBUG] 📍 Zona detectada por REGEX fallback: '{zona_detectada}'", flush=True)
                        break


            t_vec_start = time.time()
            # Optimization: Increased k to 50 to allow popular places (high reviews) to surface 
            # even if semantic match is slightly lower.
            docs = vectorstore.similarity_search(query, k=50)
            seen = set()
            candidatos_crudos = []
            for d in docs:
                nom = d.metadata.get('nombre')
                if nom and nom not in seen:
                    seen.add(nom)
                    candidatos_crudos.append(nom)
            
            t_vec_end = time.time()
            print(f"[TIMING] Vector Search took {t_vec_end - t_vec_start:.2f}s", flush=True)

            # --- HYBRID INJECTION (EXPERIMENTAL) ---
            # Si hay keywords claras, forzamos la inclusión de lugares que las mencionan mucho en reviews.
            # Esto corrige el problema de "El Tío" (mencionado 80 veces como 'milanesa' pero ignorado por vector).
            if keywords:
                print(f"[DEBUG] 💉 Hybrid Retrieval: Inyectando candidatos por keyword frequency: {keywords}", flush=True)
                for kw in keywords:
                    if len(kw) < 4: continue 
                    t_inj_start = time.time()
                    
                    # 1. Buscar menciones exactas (case insensitive)
                    # Usamos 'restaurante' y 'texto' normalizados si es posible, pero str.contains es rapido en memoria.
                    mask = df['texto'].str.contains(re.escape(kw), case=False, na=False)
                    
                    # 2. Contar menciones por local
                    if mask.any():
                        counts = df[mask]['restaurante'].value_counts()
                        # Tomamos el Top 10 de "Expertos" en esa keyword
                        top_inject = counts.head(10).index.tolist()
                        
                        count_new = 0
                        for cand in top_inject:
                            if cand not in candidatos_crudos:
                                candidatos_crudos.append(cand)
                                count_new += 1
                                print(f"[DEBUG] 💉 Inyectado: {cand} ({counts[cand]} menciones)", flush=True)
                        
                    print(f"[TIMING] Injection '{kw}' took {time.time() - t_inj_start:.2f}s", flush=True)
            # --- END HYBRID INJECTION ---

            if zona_detectada:
                candidatos_crudos = aplicar_filtro_zona(candidatos_crudos, df, zona_detectada)

            # HARD FILTER: Excluir lugares con menos de 30 reseñas (evitar lugares fantasmas o muy nuevos/malos)
            candidatos_limpios = []
            for local in candidatos_crudos:
                mask = df['restaurante'] == local
                if mask.any():
                    row = df[mask].iloc[0]
                    revs = safe_int(row.get('total_reviews_google', 0))
                    if revs >= 30:
                        candidatos_limpios.append(local)
            
            candidatos_crudos = candidatos_limpios

            # Filtering logic (same as original)
            filtro_terms = set(keywords)
            if synonyms: filtro_terms.update(synonyms)
            filtro_terms = [t.lower() for t in filtro_terms if len(t) > 3]

            grupo_alta_relevancia = []
            grupo_baja_relevancia = []

            if filtro_terms:
                import re
                patron_regex = '|'.join([re.escape(t) for t in filtro_terms])
                for local in candidatos_crudos:
                    mask = df['restaurante'] == local
                    if not mask.any(): continue
                    series_textos = df[mask]['texto'].fillna("").astype(str).str.lower()
                    if series_textos.str.contains(patron_regex, regex=True).any():
                        grupo_alta_relevancia.append(local)
                    else:
                        grupo_baja_relevancia.append(local)
            else:
                grupo_alta_relevancia = candidatos_crudos

            # 4. SELECCIÓN PARA EL JUEZ
            
            # Definimos la función de calidad PONDERADA
            def calculate_weighted_score(nombre):
                mask = df['restaurante'] == nombre
                if not mask.any(): return 0
                row = df[mask].iloc[0]
                rat = safe_float(row.get('rating_gral'))
                revs = safe_int(row.get('total_reviews_google'))
                # Log10 de 10 = 1, 100 = 2, 1000 = 3
                # User Request: Ponderar TODAVIA MAS la cantidad de reseñas. 
                # Multiplier 3.5 -> 1000 reviews adds 10.5 points! Huge boost.
                return rat + (math.log10(revs + 1) * 2.7)

            # Ordenamos ambos grupos por puntaje ponderado
            grupo_alta_relevancia.sort(key=calculate_weighted_score, reverse=True)
            grupo_baja_relevancia.sort(key=calculate_weighted_score, reverse=True)
            
            candidatos_a_verificar = []

            # Optimization: Limit candidates for Judge to 8
            candidatos_a_verificar.extend(grupo_alta_relevancia[:8])
            
            faltan = 6 - len(candidatos_a_verificar)
            if faltan > 0:
                candidatos_a_verificar.extend(grupo_baja_relevancia[:faltan])
            
            candidatos_a_verificar = candidatos_a_verificar[:8]

            # 5. EL JUEZ LLM (Verificación de Contexto)
            # Crear query limpia SIN ubicación para que el Juez solo evalúe el TIPO
            query_para_juez = query
            if zona_detectada:
                # Remover patrones de ubicación de la query
                ubicacion_patterns = [
                    r'\s*en\s+(?:el|la)\s+' + re.escape(zona_detectada),
                    r'\s*zona\s+' + re.escape(zona_detectada),
                    r'\s*del?\s+' + re.escape(zona_detectada),
                    r'\s*cerca\s+del?\s+' + re.escape(zona_detectada),
                ]
                for pattern in ubicacion_patterns:
                    query_para_juez = re.sub(pattern, '', query_para_juez, flags=re.IGNORECASE)
                query_para_juez = query_para_juez.strip()
                print(f"[DEBUG] 🧹 Query para Juez (sin ubicación): '{query_para_juez}'", flush=True)
            
            t0 = time.time()
            locales_verificados = await verificar_candidatos_con_llm(
                candidatos_a_verificar, df, query_para_juez, llm_mini
            )
            t1 = time.time()
            print(f"[TIMING] Juez LLM took {t1-t0:.2f}s", flush=True)
            
            # DEBUG: Ver qué aprobó el juez
            print(f"[DEBUG] ⚖️ Juez LLM aprobó: {len(locales_verificados)} de {len(candidatos_a_verificar)}", flush=True)
            print(f"[DEBUG] ⚖️ Aprobados: {locales_verificados}", flush=True)

            # Identificar rechazados explícitamente para no mostrarlos en 'relacionados'
            locales_rechazados = set(candidatos_a_verificar) - set(locales_verificados)
            
            # Separar EXACTOS (aprobados por juez) y RELACIONADOS (alta relevancia no aprobados)
            exactos = locales_verificados[:4]  # Máximo 4 exactos
            
            # Relacionados: tomamos de alta relevancia, excluyendo los que ya son exactos Y los que fueron RECHAZADOS por el juez
            relacionados = [
                loc for loc in grupo_alta_relevancia 
                if loc not in exactos and loc not in locales_rechazados
            ][:3]  # Máximo 3 relacionados
            
            print(f"[DEBUG] 🎯 Exactos: {exactos}", flush=True)
            print(f"[DEBUG] 🚫 Rechazados filtrados: {locales_rechazados}", flush=True)
            print(f"[DEBUG] 🔗 Relacionados: {relacionados}", flush=True)

            # 6. RANKING FINAL (Reutilizamos el score ponderado)
            exactos.sort(key=calculate_weighted_score, reverse=True)
            relacionados.sort(key=calculate_weighted_score, reverse=True)

            if not exactos and not relacionados:
                yield {"type": "meta", "mode": "rag", "cards": [], "locs": [], "zona": zona_detectada}
                yield {"type": "token", "content": "No encontré lugares que cumplan con ese requisito específico."}
                return

            # 6. GENERACIÓN PARALELA
            # Lanzamos la generación de cards en background para no bloquear el chat
            t2 = time.time()
            
            # Optimization: Generate cards for ALL places mentioned in the text to maintain consistency
            # The LLM will mention all exactos (max 3) and all relacionados (max 4)
            todos_los_locales = exactos + relacionados  # Up to 7 cards total
            
            # Helper para el contexto RÁPIDO (usando datos crudos en lugar de esperar a las cards)
            def construir_contexto_rapido(nombres, df):
                contexto = ""
                for nom in nombres:
                    mask = df['restaurante'] == nom
                    if not mask.any(): continue
                    row = df[mask].iloc[0]
                    rat = safe_float(row.get('rating_gral'))
                    revs = safe_int(row.get('total_reviews_google'))
                    # Tomamos un snippet de reseñas (crudo, pero sirve)
                    texto_raw = safe_str(row.get('texto', ''))[:400].replace("\n", " ")
                    contexto += f"- {nom} ({rat}⭐, {revs} res): {texto_raw}...\n"
                return contexto

            detalles_exactos = construir_contexto_rapido(exactos, df)
            detalles_relacionados = construir_contexto_rapido(relacionados, df)
            
            # Start background task
            card_task = asyncio.create_task(obtener_restaurant_cards(
                todos_los_locales, df, llm_mini, query, tone, 
                strict_mode=False, keywords_list=keywords, synonyms_list=synonyms
            ))

            if exactos and relacionados:
                 prompt_rag = (
                    f"{tone_system_instruction(tone)}\n"
                    f"SITUACIÓN: El usuario buscó '{query}'.\n\n"
                    f"LUGARES EXACTOS (cumplen):\n{detalles_exactos}\n\n"
                    f"LUGARES RELACIONADOS (similares):\n{detalles_relacionados}\n\n"
                    f"INSTRUCCIONES:\n"
                    "1. Recomienda los EXACTOS primero.\n"
                    "2. Menciona los RELACIONADOS como alternativa.\n"
                    "3. Usa la info provista para describir qué tienen de bueno.\n"
                    "4. IMPORTANTE: Usa Markdown. Resalta nombres de lugares con **negritas**."
                )
            elif exactos:
                prompt_rag = (
                    f"{tone_system_instruction(tone)}\n"
                    f"SITUACIÓN: El usuario buscó '{query}'.\n"
                    f"Resultados:\n{detalles_exactos}\n\n"
                    f"INSTRUCCIONES:\n"
                    "1. Confirma que encontraste lo que buscaba.\n"
                    "2. Describelos usando la info provista.\n"
                    "3. IMPORTANTE: Usa Markdown. Resalta nombres de lugares con **negritas**."
                )
            else:
                 prompt_rag = (
                    f"{tone_system_instruction(tone)}\n"
                    f"SITUACIÓN: El usuario buscó '{query}'.\n"
                    f"Solo encontré RELACIONADOS:\n{detalles_relacionados}\n\n"
                    f"INSTRUCCIONES:\n"
                    "1. Aclara que no encontraste match exacto.\n"
                    "2. Ofrece estos relacionados.\n"
                    "3. IMPORTANTE: Usa Markdown. Resalta nombres de lugares con **negritas**."
                )
            
            t_stream_start = time.time()
            print(f"[TIMING] Pre-stream setup (Parallel) took {t_stream_start - t_start:.2f}s total. Starting stream...", flush=True)
            
            # STREAMING THE RAG RESPONSE
            async for token in astream_buffer(llm_mini, prompt_rag):
                yield {"type": "token", "content": token}
            
            # AWAIT CARDS AND YIELD
            print(f"[TIMING] Text stream finished. Waiting for cards...", flush=True)
            cards = await card_task
            t3 = time.time()
            print(f"[TIMING] Card Gen finished at {t3 - t_start:.2f}s total (Latencia oculta)", flush=True)
            
            nombres_finales = [c.nombre for c in cards]
            locs = obtener_coordenadas(nombres_finales, df)
            
            # Yield Metadata at the END
            yield {"type": "meta", "mode": "rag", "cards": cards, "locs": locs, "zona": zona_detectada}
            return

        except Exception as e:
            logger.error(f"Error RAG: {e}")
            yield {"type": "meta", "mode": "rag"}
            yield {"type": "token", "content": "Tuve un problema técnico buscando eso."}
            return

    # --- CAMINO D: GENERAL ---
    if intencion == "BLOCK":
        ctx['strikes'] = strikes + 1
        yield {"type": "meta", "mode": "general"}
        yield {"type": "token", "content": "Epa, bajemos un cambio. Mantené el respeto, estoy acá para ayudar. (Strike sumado)"}
        return
    
    # GENERAL
    try:
        prefix = tone_system_instruction(tone)
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
        
        yield {"type": "meta", "mode": "general"}
        async for token in astream_buffer(llm_mini, prompt_chat):
            yield {"type": "token", "content": token}
            
    except Exception as e:
        logger.error(f"Error General: {e}")
        yield {"type": "meta", "mode": "general"}
        yield {"type": "token", "content": "¡Buenas! ¿En qué te puedo ayudar para comer hoy?"}

async def procesar_consulta(query, df, vectorstore, llm_mini, llm_smart, ctx=None, user_ip=None):
    """
    Wrapper legacy that consumes the generator and returns the full response tuple.
    Returns: (resp, mode, pend, locs, cards, det)
    """
    full_text = ""
    mode = "general"
    cards = []
    locs = []
    pend = None
    det = "" # Not really used in gen yet, but kept for signature
    
    async for event in procesar_consulta_gen(query, df, vectorstore, llm_mini, llm_smart, ctx, user_ip):
        if event["type"] == "token":
            full_text += event["content"]
        elif event["type"] == "meta":
            if "mode" in event: mode = event["mode"]
            if "cards" in event: cards = event["cards"]
            if "locs" in event: locs = event["locs"]
            if "pending" in event: pend = event["pending"]
            # Intent is not returned in tuple
    
    return full_text, mode, pend, locs, cards, det

        
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

@app.post("/chat/stream")
async def chat_stream(req: QueryRequest, request: Request):
    start_time = asyncio.get_event_loop().time()
    
    # 1. Setup Context
    ctx = req.conversation_context.copy() if req.conversation_context else {}
    if req.tone: ctx['tone'] = sanitize_tone(req.tone)
    client_ip = extract_client_ip(request)

    async def event_generator():
        # Accumulators for Logging and Context
        full_text = ""
        mode = "general"
        cards = []
        locs = []
        pend = None
        zona = None
        
        try:
            async for event in procesar_consulta_gen(req.query, df, vectorstore, llm_mini, llm_smart, ctx, user_ip=client_ip):
                # Update accumulators
                if event["type"] == "token":
                    full_text += event["content"]
                elif event["type"] == "meta":
                    if "mode" in event: mode = event["mode"]
                    if "cards" in event: 
                        cards = event["cards"]
                        # Convert Pydantic models to dicts for JSON serialization
                        event["cards"] = [c.model_dump() if hasattr(c, 'model_dump') else c.dict() for c in cards]
                    if "locs" in event: locs = event["locs"]
                    if "pending" in event: pend = event["pending"]
                    if "zona" in event: zona = event["zona"]
                
                # Encode and yield
                yield json.dumps(event, ensure_ascii=False) + "\n"
        except Exception as e:
             logger.error(f"Stream error: {e}")
             yield json.dumps({"type": "error", "message": str(e)}, ensure_ascii=False) + "\n"

        # === POST-STREAMING LOGIC ===
        
        # 1. Update Context (Same logic as sync chat)
        new_ctx = ctx.copy()
        if pend: new_ctx['pending_options'] = pend
        elif 'pending_options' in new_ctx: del new_ctx['pending_options']
        
        if req.conversation_context and 'original_query' in req.conversation_context and 'original_query' not in new_ctx:
                new_ctx['original_query'] = req.conversation_context['original_query']

        # Yield final context update event
        yield json.dumps({"type": "context_update", "context": new_ctx}, ensure_ascii=False) + "\n"

        # 2. Logging
        response_time = asyncio.get_event_loop().time() - start_time
        restaurants = [c.nombre for c in cards] if cards else []
        
        ai_provider = None
        if llm_mini: ai_provider = f"Mini:{llm_mini.model_name}"
        if llm_smart and mode in ["rag", "resumen"]: ai_provider = f"Smart:{llm_smart.model_name}"

        asyncio.create_task(log_user_query_to_discord(
            request, 
            req.query, 
            tone=req.tone,
            response_time=response_time,
            mode=mode,
            restaurants=restaurants,
            keywords=None,
            used_cache=False,
            ai_provider=ai_provider,
            context_info=ctx,
            strikes=ctx.get('strikes', 0),
            zona_detectada=zona
        ))
        
    return StreamingResponse(event_generator(), media_type="application/x-ndjson")

@app.post("/chat", response_model=QueryResponse)
async def chat(req: QueryRequest, request: Request):
    start_time = asyncio.get_event_loop().time()
    
    try:
        # LOG DE ENTRADA: ¿Qué contexto me manda el frontend?
        logger.info(f"📥 Contexto Recibido: {req.conversation_context}")
        ctx = req.conversation_context.copy() if req.conversation_context else {}
        if req.tone: ctx['tone'] = sanitize_tone(req.tone)

        client_ip = extract_client_ip(request)
        
        resp, mode, pend, locs, cards, det = await procesar_consulta(
            req.query, df, vectorstore, llm_mini, llm_smart, ctx, user_ip=client_ip
        )
        
        # Calcular tiempo de respuesta
        response_time = asyncio.get_event_loop().time() - start_time
        
        # Extraer nombres de restaurantes retornados
        restaurants = [c.nombre for c in cards] if cards else []
        
        # Determinar proveedor de IA usado
        ai_provider = None
        if llm_mini:
            ai_provider = f"Mini:{llm_mini.model_name}"
        if llm_smart and mode in ["rag", "resumen"]:
            ai_provider = f"Smart:{llm_smart.model_name}"
        
        # Log asíncrono con todas las métricas
        asyncio.create_task(log_user_query_to_discord(
            request, 
            req.query, 
            tone=req.tone,
            response_time=response_time,
            mode=mode,
            restaurants=restaurants,
            keywords=None,  # Se podría extraer del análisis semántico
            used_cache=False,  # Se podría detectar si vino de caché
            ai_provider=ai_provider,
            context_info=ctx,
            strikes=ctx.get('strikes', 0)
        ))
        
        new_ctx = ctx.copy() if ctx else {}
        if pend: new_ctx['pending_options'] = pend
        elif 'pending_options' in new_ctx: del new_ctx['pending_options']
        
        # if req.conversation_context and 'last_entity' in req.conversation_context and 'last_entity' not in new_ctx:
        #      new_ctx['last_entity'] = req.conversation_context['last_entity']
        if req.conversation_context and 'original_query' in req.conversation_context and 'original_query' not in new_ctx:
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