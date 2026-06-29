import os
import sys

# Force UTF-8 on Windows for emoji printing
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        # Older python versions might not have reconfigure
        pass

import json
import re
import unicodedata
import asyncio
import logging
import math
import time  # Added for debugging
import urllib.request
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from zoneinfo import ZoneInfo
import pandas as pd
import numpy as np
import gc
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_postgres import PGVector
from sqlalchemy import create_engine
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from upstash_redis.asyncio import Redis
from rapidfuzz import process, fuzz


# ==========================================
# 0. CONFIGURACIÓN DE LOGS
# ==========================================
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
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

# Versión del servidor — actualizar manualmente al hacer cambios significativos
SERVER_VERSION = (
    "v8.0"  # 2026-03-06: LAZY reviews architecture — only df_lugares (936 rows) in memory
)

logger.info(f"🔌 Iniciando BACKEND {SERVER_VERSION} con Colección: '{COLLECTION_NAME}'")


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
        if not key:
            return "unknown"
        return str(key).lower().strip().replace(" ", "_")

    async def get_json(self, prefix, key):
        if not self.client:
            return None
        full_key = f"{prefix}:{self._sanitize_key(key)}"
        try:
            data = await self.client.get(full_key)
            if data:
                return data if isinstance(data, dict) else json.loads(data)
            return None
        except:
            return None

    async def set_json(self, prefix, key, value_dict, expire=604800):
        if not self.client:
            return
        full_key = f"{prefix}:{self._sanitize_key(key)}"
        try:
            json_str = json.dumps(value_dict, ensure_ascii=False)
            await self.client.set(full_key, json_str, ex=expire)
        except:
            pass

    async def set_value(self, key, value, expire=None):
        if not self.client:
            return
        try:
            await self.client.set(key, value, ex=expire)
        except:
            pass

    async def get_value(self, key):
        if not self.client:
            return None
        try:
            return await self.client.get(key)
        except:
            return None


cache = RedisCacheManager(UPSTASH_REDIS_REST_URL, UPSTASH_REDIS_REST_TOKEN)

# Variables globales
df = None
df_lugares = None
db_engine = None  # SQLAlchemy engine for lazy-load queries
vectorstore = None
llm_mini = None
llm_smart = None

# ==========================================
# MAPEO DE KEYWORDS GENÉRICAS A CATEGORÍAS
# Para queries como "bares", "pizzerias", "sushi" -> filtrar por categoría directa
# ==========================================
KEYWORD_TO_CATEGORIES = {
    # Bares y cervecerías
    "bar": [
        "Bar",
        "Bar & grill",
        "Beer garden",
        "Beer hall",
        "Brewery",
        "Brewpub",
        "Gastropub",
        "Lounge",
        "Piano bar",
        "Wine bar",
    ],
    "bares": [
        "Bar",
        "Bar & grill",
        "Beer garden",
        "Beer hall",
        "Brewery",
        "Brewpub",
        "Gastropub",
        "Lounge",
        "Piano bar",
        "Wine bar",
    ],
    "cerveceria": ["Brewery", "Brewpub", "Beer hall", "Beer garden"],
    "cerveceria": ["Brewery", "Brewpub", "Beer hall", "Beer garden"],
    "pub": ["Brewpub", "Gastropub", "Bar"],
    # Pizzerías
    "pizza": ["Pizza restaurant", "Pizza delivery", "Pizza Takeout"],
    "pizzeria": ["Pizza restaurant", "Pizza delivery", "Pizza Takeout"],
    "pizzerias": ["Pizza restaurant", "Pizza delivery", "Pizza Takeout"],
    # Sushi / Japonés
    "sushi": ["Sushi restaurant", "Sushi takeaway", "Japanese restaurant"],
    "japones": ["Japanese restaurant", "Sushi restaurant", "Noodle shop"],
    # Heladerías
    "helado": ["Ice cream shop"],
    "heladeria": ["Ice cream shop"],
    "helados": ["Ice cream shop"],
    # Cafeterías
    "cafe": ["Cafe", "Coffee shop", "Cafeteria", "Espresso bar"],
    "cafeteria": ["Cafe", "Coffee shop", "Cafeteria"],
    # Parrillas / Carnes
    "parrilla": [
        "Barbecue restaurant",
        "Steak house",
        "Grill",
        "Chophouse restaurant",
        "Argentinian restaurant",
    ],
    "asado": ["Barbecue restaurant", "Steak house", "Grill", "Argentinian restaurant"],
    "carne": ["Steak house", "Barbecue restaurant", "Chophouse restaurant"],
    # Hamburguesas
    "hamburguesa": ["Hamburger restaurant", "Fast food restaurant", "Bar & grill"],
    "hamburgueserias": ["Hamburger restaurant", "Fast food restaurant"],
    # Mariscos / Pescados
    "mariscos": ["Seafood restaurant", "Fish restaurant"],
    "pescado": ["Seafood restaurant", "Fish restaurant"],
    # Pastas / Italiano
    "pasta": ["Italian restaurant", "Pasta shop"],
    "italiano": ["Italian restaurant", "Pasta shop"],
    # Vegetariano / Vegano
    "vegano": ["Vegan restaurant", "Vegetarian restaurant", "Health food restaurant"],
    "vegetariano": [
        "Vegetarian restaurant",
        "Vegan restaurant",
        "Health food restaurant",
    ],
    # Panaderías / Pastelerías
    "panaderia": ["Bakery", "Pastry shop", "Patisserie"],
    "pasteleria": ["Pastry shop", "Patisserie", "Cake shop", "Bakery"],
    "tortas": ["Cake shop", "Pastry shop", "Patisserie"],
    # Mexicano
    "mexicano": ["Mexican restaurant", "Pueblan restaurant"],
    "tacos": ["Mexican restaurant"],
}


# ==========================================
# 2. LIFESPAN
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    global df, df_lugares, vectorstore, llm_mini, llm_smart, db_engine
    startup_dt = datetime.now(ZoneInfo("America/Argentina/Buenos_Aires")).strftime(
        "%Y-%m-%d %H:%M:%S ART"
    )
    logger.info(f"☁️ Servidor iniciado | {SERVER_VERSION} | {startup_dt}")

    # Notificar activación del backend
    try:
        await _send_discord_webhook(
            "🟢 **Backend ACTIVADO** - Servidor iniciado en Fly.io"
        )
    except Exception as e:
        logger.warning(f"No se pudo notificar startup a Discord: {e}")

    if DATABASE_URL:
        try:
            db_engine = create_engine(DATABASE_URL)

            # LAZY ARCHITECTURE v8: Solo cargamos df_lugares (936 filas)
            # Las reviews (155k+) se consultan bajo demanda por restaurante.
            query_lugares = """
                SELECT nombre as restaurante, rating_gral, total_reviews_google, direccion,
                       latitud, longitud, barrio, zona, categoria, resumen_reviews
                FROM lugares
            """
            df_lugares = pd.read_sql(query_lugares, db_engine)
            logger.info(f"📊 Lugares cargados: {len(df_lugares)} locales")

            # Limpiar columnas ligeras
            cols_ligeras = ["restaurante", "direccion", "barrio", "zona"]
            for col in cols_ligeras:
                if col in df_lugares.columns:
                    df_lugares[col] = df_lugares[col].fillna("").str.strip()
            if "resumen_reviews" in df_lugares.columns:
                df_lugares["resumen_reviews"] = df_lugares["resumen_reviews"].fillna("")

            # Convertir rating_gral
            if "rating_gral" in df_lugares.columns:
                rating_raw = df_lugares["rating_gral"].to_numpy(dtype=str, na_value="0")
                rating_clean = [v.replace(",", ".") for v in rating_raw]
                df_lugares["rating_gral"] = pd.Series(
                    pd.to_numeric(rating_clean, errors="coerce"), index=df_lugares.index
                ).fillna(0.0)

            # Indexar por nombre de restaurante para O(1) lookups
            if not df_lugares.empty and "restaurante" in df_lugares.columns:
                df_lugares.set_index("restaurante", inplace=True, drop=False)

            # df ya no se carga; se usa None como señal para lazy-load
            df = None

            gc.collect()
            logger.info(f"✅ df_lugares indexado: {len(df_lugares)} locales. Reviews: LAZY MODE.")
        except Exception as e:
            logger.error(f"❌ Error cargando datos: {e}")
            df_lugares = pd.DataFrame()
            df = None
    else:
        df_lugares = pd.DataFrame()
        df = None

    try:
        embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
        vectorstore = PGVector(
            connection=DATABASE_URL,
            embeddings=embeddings,
            collection_name=COLLECTION_NAME,
            use_jsonb=True,
        )

        provider_mode = os.getenv("AI_PROVIDER", "hybrid").lower()
        deepseek_key = os.getenv("DEEPSEEK_API_KEY")
        openai_key = os.getenv("OPENAI_API_KEY")

        logger.info(f"🤖 Configurando LLMs en modo: {provider_mode.upper()}")

        openai_mini = ChatOpenAI(
            model="gpt-4o-mini", temperature=0, api_key=openai_key, max_tokens=1024
        )
        openai_smart = ChatOpenAI(
            model="gpt-4o", temperature=0, api_key=openai_key, max_tokens=1024
        )

        ds_instance = None
        if deepseek_key:
            ds_instance = ChatOpenAI(
                model="deepseek-chat",
                openai_api_key=deepseek_key,
                openai_api_base="https://api.deepseek.com",
                temperature=0,
                max_tokens=1024,
            )

        if provider_mode == "deepseek":
            if not ds_instance:
                raise ValueError("Falta DEEPSEEK_API_KEY")
            llm_mini = ds_instance
            llm_smart = ds_instance
        elif provider_mode == "openai":
            llm_mini = openai_mini
            llm_smart = openai_smart
        else:  # hybrid
            llm_mini = openai_mini
            llm_smart = ds_instance if ds_instance else openai_smart

        logger.info(
            f"✅ IA lista. Mini: {llm_mini.model_name} | Smart: {llm_smart.model_name}"
        )

    except Exception as e:
        logger.error(f"❌ Error iniciando LLMs: {e}")
        llm_mini = ChatOpenAI(model="gpt-4o-mini", temperature=0)
        llm_smart = ChatOpenAI(model="gpt-4o-mini", temperature=0)

    yield

    # Notificar desactivación del backend
    logger.info("🛑 Apagando servidor...")
    try:
        await _send_discord_webhook(
            "🔴 **Backend DETENIDO** - Servidor apagado por inactividad (Fly.io)"
        )
    except Exception as e:
        logger.warning(f"No se pudo notificar shutdown a Discord: {e}")


# ==========================================
# LAZY-LOAD: Reviews bajo demanda desde PostgreSQL
# ==========================================
def _fetch_reviews_sync(nombres: list, limit_per_local: int = 15):
    """Sync worker for fetching reviews from DB. Runs in thread."""
    global db_engine
    if db_engine is None or not nombres:
        return pd.DataFrame()
    
    # Use parameterized query for safety
    placeholders = ", ".join([f"'{n.replace(chr(39), chr(39)+chr(39))}'" for n in nombres])
    query = f"""
        SELECT restaurante, autor, rating_user, texto, fecha
        FROM (
            SELECT restaurante, autor, rating_user, texto, fecha_aproximada as fecha,
                   ROW_NUMBER() OVER(PARTITION BY restaurante ORDER BY fecha_aproximada DESC) as rn
            FROM reviews
            WHERE restaurante IN ({placeholders})
        ) t
        WHERE rn <= {limit_per_local}
    """
    try:
        result = pd.read_sql(query, db_engine)
        if not result.empty and "texto" in result.columns:
            result["texto"] = result["texto"].fillna("")
        return result
    except Exception as e:
        logger.error(f"Error lazy-loading reviews: {e}")
        return pd.DataFrame()


async def obtener_reviews_por_local(nombres: list, limit_per_local: int = 15):
    """
    LAZY-LOAD: Trae reviews de PostgreSQL solo para los restaurantes necesarios.
    Se ejecuta en un thread para no bloquear el event loop.
    Retorna un DataFrame indexado por 'restaurante' (compatible con el viejo df).
    """
    if not nombres:
        return pd.DataFrame()
    
    result = await asyncio.to_thread(_fetch_reviews_sync, nombres, limit_per_local)
    
    if not result.empty and "restaurante" in result.columns:
        result.set_index("restaurante", inplace=True, drop=False)
        result.sort_index(inplace=True)
    
    return result


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
        asyncio.create_task(cache_instance.set_json("resumen_texto", cache_key, buffer))


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
    if pd.isna(val) or val is None:
        return ""
    return str(val).strip()


def safe_float(val):
    try:
        v = float(val) if pd.notna(val) else 0.0
        return 0.0 if (v != v) else v  # NaN check: NaN != NaN
    except:
        return 0.0


def safe_int(val):
    try:
        return int(float(val)) if pd.notna(val) else 0
    except:
        return 0


def _truncate_text(text: str, max_len: int) -> str:
    text = safe_str(text)
    return (text[: max_len - 1] + "…") if len(text) > max_len else text


def extract_client_ip(request: Request) -> str:
    try:
        headers = request.headers
        for key in (
            "cf-connecting-ip",
            "true-client-ip",
            "x-real-ip",
            "fly-client-ip",
            "x-forwarded-for",
        ):
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
    if any(
        x in ua
        for x in ["mobile", "android", "iphone", "ipod", "blackberry", "windows phone"]
    ):
        device = "📱 Mobile"
    elif "tablet" in ua or "ipad" in ua:
        device = "📱 Tablet"
    else:
        device = "💻 Desktop"

    # Detectar navegador
    if "edg" in ua:
        browser = "Edge"
    elif "chrome" in ua and "chromium" not in ua:
        browser = "Chrome"
    elif "firefox" in ua:
        browser = "Firefox"
    elif "safari" in ua and "chrome" not in ua:
        browser = "Safari"
    elif "opera" in ua or "opr" in ua:
        browser = "Opera"
    else:
        browser = "Otro"

    # Detectar SO
    if "windows" in ua:
        os_name = "Windows"
    elif "mac os" in ua or "macos" in ua:
        os_name = "macOS"
    elif "android" in ua:
        os_name = "Android"
    elif "iphone" in ua or "ipad" in ua or "ipod" in ua:
        os_name = "iOS"
    elif "linux" in ua:
        os_name = "Linux"
    else:
        os_name = "Desconocido"

    return {
        "device": device,
        "browser": browser,
        "os": os_name,
        "raw": user_agent[:100] if user_agent else "Unknown",
    }


async def get_ip_location(ip: str) -> dict:
    """Obtiene país y ciudad desde IP usando theipapi.com"""
    # Detectar IPs locales/privadas (IPv4 e IPv6)
    if not ip or ip == "unknown":
        logger.info(f"🏠 IP desconocida, usando ubicación por defecto")
        return {"country": "Desconocido", "city": "", "flag": "❓"}

    # IPs privadas IPv4
    if ip.startswith(
        (
            "127.",
            "192.168.",
            "10.",
            "172.16.",
            "172.17.",
            "172.18.",
            "172.19.",
            "172.20.",
            "172.21.",
            "172.22.",
            "172.23.",
            "172.24.",
            "172.25.",
            "172.26.",
            "172.27.",
            "172.28.",
            "172.29.",
            "172.30.",
            "172.31.",
        )
    ):
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
                raw = resp.read().decode("utf-8")
                logger.info(f"📥 Respuesta API: {raw[:200]}...")  # Log truncado
                return json.loads(raw)

        data = await asyncio.to_thread(_fetch_sync)

        # theipapi.com devuelve datos en body.location
        if data.get("status") == "OK" and "body" in data:
            body = data["body"]
            location = body.get("location", {})

            country = location.get("country", "Desconocido")
            city = location.get("city", "")
            code = location.get("country_code", "")

            # Emojis de banderas (offset desde 🇦 = U+1F1E6)
            flag = "🌍"
            if code and len(code) == 2:
                try:
                    flag = "".join(
                        chr(0x1F1E6 + ord(c) - ord("A")) for c in code.upper()
                    )
                except:
                    flag = "🌍"

            logger.info(f"✅ Geolocalización exitosa: {flag} {country} ({city})")
            return {"country": country, "city": city, "flag": flag}
        else:
            logger.warning(
                f"⚠️ API theipapi.com respuesta inesperada: {data.get('status')}"
            )

    except Exception as e:
        logger.error(
            f"❌ Error obteniendo geolocalización de IP {ip}: {e}", exc_info=True
        )

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
    zona_detectada: str = None,
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
    if location["city"]:
        loc_str += f" ({location['city']})"

    # Mensaje Discord (compacto, query primero)
    zona_str = zona_detectada.title() if zona_detectada else "Todo Neuquén"

    discord_parts = [
        f"💬 **{q}**",  # Query PRIMERO y destacada
        f"🗺️ Zona: {zona_str}",
        f"🕒 {ts}",
        f"📍 {loc_str}",
        f"{ua_info['device']} {ua_info['browser']} ({ua_info['os']})",
        f"🎭 {tone_display}",
    ]

    if response_time:
        discord_parts.append(f"⏱️ {response_time:.2f}s")

    if mode:
        mode_emoji = {
            "rag": "🔍",
            "resumen": "📋",
            "estadisticas": "📊",
            "blocked": "🚫",
        }.get(mode, "💬")
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
        "country": location["country"],
        "city": location["city"],
        "device": ua_info["device"].replace("📱 ", "").replace("💻 ", ""),
        "browser": ua_info["browser"],
        "os": ua_info["os"],
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
        "last_entity": context_info.get("last_entity") if context_info else None,
        "strikes": strikes,
        "user_agent": ua_info["raw"],
    }

    try:
        with open("user_queries.log", "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning(f"⚠️ No se pudo guardar log a archivo: {e}")


def formatear_autor(nombre):
    nombre = safe_str(nombre)
    if not nombre or nombre.lower() == "nan":
        return "Anónimo"
    partes = nombre.split()
    return f"{partes[0]} {partes[1][0]}." if len(partes) > 1 else partes[0]


def fecha_a_orden(fecha_str):
    fecha_str = safe_str(fecha_str).lower()
    if not fecha_str:
        return 9999
    numeros = re.findall(r"\d+", fecha_str)
    num = int(numeros[0]) if numeros else 1
    if "hora" in fecha_str:
        return num
    if "día" in fecha_str:
        return num * 24
    if "semana" in fecha_str:
        return num * 168
    if "mes" in fecha_str:
        return num * 720
    if "año" in fecha_str:
        return num * 8760
    return 5000


def obtener_coordenadas(nombres, df=None):
    """
    Usa df_lugares (ya indexado) para buscar lat/lng de forma instantánea O(1).
    Esto evita escanear 180k filas de reseñas solo para buscar coordenadas.
    """
    global df_lugares
    locs = []
    
    # Si df_lugares no está indexado aún (fallback), buscamos linearmente en df
    if df_lugares.index.name != "restaurante":
        if df is None or df.empty: return []
        for nom in nombres:
            if not nom: continue
            mask = df["restaurante"].str.lower() == nom.lower()
            if mask.any():
                r = df[mask].iloc[0]
                lat = safe_float(r.get("latitud"))
                lng = safe_float(r.get("longitud"))
                if lat != 0 and lng != 0:
                    locs.append({
                        "nombre": safe_str(r.get("restaurante")),
                        "lat": lat,
                        "lng": lng,
                        "direccion": safe_str(r.get("direccion")),
                        "rating": safe_float(r.get("rating_gral")),
                        "total_reviews": safe_int(r.get("total_reviews_google"))
                    })
        return locs

    # Método optimizado O(1)
    for nom in nombres:
        if not nom or nom not in df_lugares.index:
            continue
        
        r = df_lugares.loc[nom]
        if isinstance(r, pd.DataFrame):
            r = r.iloc[0]
            
        lat = safe_float(r.get("latitud"))
        lng = safe_float(r.get("longitud"))
        if lat != 0 and lng != 0:
            locs.append({
                "nombre": safe_str(nom),
                "lat": lat,
                "lng": lng,
                "direccion": safe_str(r.get("direccion", "")),
                "rating": safe_float(r.get("rating_gral", 0.0)),
                "total_reviews": safe_int(r.get("total_reviews_google", 0)),
            })
    return locs


def sanitize_tone(t):
    if not t:
        return "cordial"
    tt = str(t).lower().strip()
    return tt if tt in {"cordial", "soberbio", "sassy"} else "cordial"


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
    if tone == "cordial":
        return (
            f"{base}\n"
            "Personalidad: Amigable, servicial y empático. Como ese amigo que siempre te tira la posta con buena onda.\n"
            "Objetivo: Que el usuario se sienta bienvenido y encuentre lo que busca sin vueltas."
        )

    if tone == "soberbio":
        return (
            f"{base}\n"
            "Personalidad: 'Tincho' de clase alta, snob gastronómico y levemente pedante.\n"
            "Estilo: Usá palabras como 'básico', 'pretencioso', 'top', 'exclusive'. "
            "Mirás un poco por encima del hombro a los lugares comunes, pero reconocés la calidad cuando la ves.\n"
            "Ejemplo: 'O sea, si te gusta la comida recalentada, allá vos... pero yo iría a otro lado'."
        )

    if tone == "sassy":
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
        "travesti",
        "travestis",
        "travesaño",
        "travesaños",
        "teta",
        "tetas",
        "culo",
        "pito",
        "pene",
        "pija",
        "pijas",
        "sexo",
        "puta",
        "puto",
        "concha",
        "verga",
        "porno",
        "xxx",
        "droga",
        "cocaina",
        "marihuana",
        "falopa",
        "merca",
        "porro",
    ]
    q_norm = query.lower()
    for word in banned_words:
        # Buscamos la palabra exacta o rodeada de espacios/signos
        pattern = r"(?<!\w)" + re.escape(word) + r"(?!\w)"
        # Pero para plurales simples como 'tetas' o 'travestis' la lista ya los tiene
        if word in q_norm:
            return True
    return False


def get_keywords_from_topic(topic):
    if not topic:
        return []
    stopwords = {
        "de",
        "la",
        "el",
        "en",
        "y",
        "que",
        "los",
        "las",
        "un",
        "una",
        "del",
        "para",
        "con",
        "donde",
        "hay",
        "lugar",
        "lugares",
        "comer",
        "mejor",
        "mejores",
        "neuquen",
        "que",
        "qué",
        "tal",
        "como",
    }
    words = safe_str(topic).lower().split()
    clean_words = [w for w in words if w not in stopwords and len(w) > 2]
    stemmed_words = []
    for w in clean_words:
        if w.endswith("es") and len(w) > 4:
            stemmed_words.append(w[:-2])
        elif w.endswith("s") and len(w) > 3:
            stemmed_words.append(w[:-1])
        else:
            stemmed_words.append(w)
    return stemmed_words


def rankear_reviews_por_topico(df_reviews, topic=None):
    df_local = df_reviews.copy()
    df_local.loc[:, "orden_fecha"] = df_local["fecha"].apply(fecha_a_orden)
    if "rating_user" in df_local.columns:
        df_local.loc[:, "rating_user"] = (
            pd.to_numeric(df_local["rating_user"], errors="coerce")
            .fillna(0)
            .astype(int)
        )
    else:
        df_local.loc[:, "rating_user"] = 0

    if not topic or len(topic) < 3:
        return df_local.sort_values(
            ["orden_fecha", "rating_user"], ascending=[True, False]
        )

    keywords = get_keywords_from_topic(topic)
    if not keywords:
        return df_local.sort_values("orden_fecha")

    def calcular_relevancia(row):
        texto = safe_str(row.get("texto")).lower()
        rating = row.get("rating_user", 0)
        score = 0
        match_found = False
        for k in keywords:
            if k in texto:
                score += 100
                match_found = True
        if match_found:
            if rating >= 4:
                score += 50
            elif rating <= 2:
                score -= 20
        return score

    df_local["score_topic"] = df_local.apply(calcular_relevancia, axis=1)
    if df_local["score_topic"].max() == 0:
        return df_local.sort_values("orden_fecha")
    return df_local.sort_values(["score_topic", "orden_fecha"], ascending=[False, True])


def seleccionar_mejor_review(df_local, topic_query=None):
    sorted_df = rankear_reviews_por_topico(df_local, topic_query)
    if sorted_df.empty:
        return None
    if topic_query:
        top_match = sorted_df.iloc[0]
        if top_match["score_topic"] > 0:
            if len(safe_str(top_match["texto"])) >= 4:
                return top_match
        return None
    candidatas = sorted_df[sorted_df["texto"].str.len() > 25]
    if not candidatas.empty:
        return candidatas.iloc[0]
    return sorted_df.iloc[0]


async def generar_descripcion_async(llm, nombre, sample, tone="cordial"):
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
        return res.content.strip().replace('"', "")
    except:
        return "Restaurante popular en Neuquén."


def obtener_reviews_tematicas(df_local, keywords, limit=8):
    """
    Busca en TODAS las reviews del local aquellas que contengan las keywords.
    """
    if not keywords:
        return df_local.head(limit)

    # Creamos un patrón Regex: (pelotero|juegos|niños)
    # Re.escape asegura que caracteres raros no rompan el regex
    pattern = "|".join([re.escape(k) for k in keywords if len(k) > 2])

    if not pattern:
        return df_local.head(limit)

    mask = df_local["texto"].str.lower().str.contains(pattern, regex=True, na=False)
    reviews_tematicas = df_local[mask]

    if not reviews_tematicas.empty:
        # Priorizamos estas reviews específicas
        return reviews_tematicas.head(limit)
    else:
        # Si no hay mención explícita, devolvemos las generales
        return df_local.head(limit)


async def obtener_restaurant_cards(
    nombres_restaurantes,
    df_lugares_ref,
    llm,
    query_context=None,
    tone="cordial",
    strict_mode=True,
    keywords_list=None,
    synonyms_list=None,
):
    """
    LAZY v8: Usa df_lugares para metadata y resumen_reviews para sample text.
    Ya no depende del DataFrame de 155k reviews.
    """
    cards = []
    tasks = []

    # 1. Armamos la lista completa de términos de búsqueda
    search_terms = set()
    if keywords_list:
        for k in keywords_list:
            search_terms.add(k.lower())
    if synonyms_list:
        for s in synonyms_list:
            search_terms.add(s.lower())
    if not search_terms and query_context:
        search_terms.add(query_context.lower())

    final_search_terms = [k for k in search_terms if len(k) > 3]

    # 1.5 Fetch real reviews para usarlos como cita destacada en las cards
    real_reviews_df = await obtener_reviews_por_local(nombres_restaurantes, limit_per_local=3)

    card_items = []

    for nombre in nombres_restaurantes:
        if not nombre or nombre not in df_lugares_ref.index:
            continue

        row = df_lugares_ref.loc[nombre]
        if isinstance(row, pd.DataFrame): row = row.iloc[0]
        nombre_real = safe_str(row.get("restaurante", nombre))

        # 2. SAMPLE TEXT desde resumen_reviews para el LLM (descripción general)
        resumen = safe_str(row.get("resumen_reviews", ""))
        sample_text = resumen[:3000] if (resumen and len(resumen) > 30) else f"Restaurante en Neuquén con {safe_int(row.get('total_reviews_google', 0))} reseñas."
        
        # 3. Extraer una reseña real para la tarjeta (frase destacada)
        real_review_text = ""
        real_review_autor = "Google Reviews"
        
        if not real_reviews_df.empty and nombre_real in real_reviews_df.index:
            rest_reviews = real_reviews_df.loc[[nombre_real]]
            if not rest_reviews.empty:
                for _, rv in rest_reviews.iterrows():
                    if len(str(rv.get("texto", ""))) > 20:
                        real_review_text = str(rv["texto"])
                        real_review_autor = formatear_autor(str(rv.get("autor", "Google Reviews")))
                        break
                        
        if real_review_text:
            frase = f'"{real_review_text[:200]}..."'
            autor = real_review_autor
        else:
            frase = resumen[:200] + "..." if resumen else sample_text
            autor = "Google Reviews"

        # 3. Preparación para CACHE GET
        topic_key = "-".join(sorted(list(search_terms))) if search_terms else "general"
        cache_key = f"desc_{nombre_real}_{topic_key}_{sanitize_tone(tone)}"
        
        card_items.append({
            "nombre": nombre, "nombre_real": nombre_real, "row": row, 
            "sample_text": sample_text, "frase": frase, "autor": autor, 
            "cache_key": cache_key
        })

    # Ejecución Async CACHÉ GET
    cache_tasks = [cache.get_json("desc", item["cache_key"]) for item in card_items]
    cached_descs = await asyncio.gather(*cache_tasks) if cache_tasks else []

    contexto_extra = f"El usuario busca conceptos relacionados con: '{', '.join(final_search_terms)}'. Si aparecen, MENCIONALO." if final_search_terms else ""

    tasks = []
    for i, item in enumerate(card_items):
        desc = cached_descs[i] if i < len(cached_descs) else None
        if desc:
            tasks.append({
                "type": "cached", "val": desc, "row": item["row"],
                "frase": item["frase"], "autor": item["autor"], "nombre_real": item["nombre_real"]
            })
        else:
            task_coro = generar_descripcion_async_tematica(
                llm, item["nombre_real"], item["sample_text"], tone, contexto_extra
            )
            tasks.append({
                "type": "generate", "val": task_coro, "row": item["row"],
                "frase": item["frase"], "autor": item["autor"], "nombre_real": item["nombre_real"],
                "cache_key": item["cache_key"],
            })

    # Ejecución Async de LLM Generations
    generations_needed = [t["val"] for t in tasks if t["type"] == "generate"]
    if generations_needed:
        results = await asyncio.gather(*generations_needed)

    gen_idx = 0
    cards = []
    for item in tasks:
        if item["type"] == "cached":
            descripcion = item["val"]
        else:
            descripcion = results[gen_idx]
            gen_idx += 1
            asyncio.create_task(cache.set_json("desc", item["cache_key"], descripcion))

        cards.append(
            RestaurantCard(
                nombre=item["nombre_real"],
                rating=safe_float(item["row"].get("rating_gral")),
                total_reviews=safe_int(item["row"].get("total_reviews_google")),
                direccion=safe_str(item["row"].get("direccion")),
                barrio=safe_str(item["row"].get("barrio")),
                zona=safe_str(item["row"].get("zona")),
                categoria=safe_str(item["row"].get("categoria")),
                descripcion=safe_str(descripcion),
                frase_destacada=safe_str(item["frase"]),
                autor_reseña=safe_str(item["autor"]),
            )
        )

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
        return res.content.strip().replace('"', "")
    except:
        return f"Restaurante popular en Neuquén: {nombre}."


def obtener_restaurant_cards_simple(nombres_restaurantes, df):
    cards = []
    for nombre in nombres_restaurantes:
        if not nombre:
            continue
        mask = df["restaurante"].str.lower() == nombre.lower()
        if mask.any():
            row = df[mask].iloc[0]
            cards.append(
                RestaurantCard(
                    nombre=safe_str(row["restaurante"]),
                    rating=safe_float(row.get("rating_gral")),
                    total_reviews=safe_int(row.get("total_reviews_google")),
                    categoria=safe_str(row.get("categoria")),
                )
            )
    cards.sort(key=lambda x: x.rating, reverse=True)
    return cards


# ==========================================
# 5. INTENCIÓN Y DETECCIÓN (BRAIN)
# ==========================================


async def analizar_query_semantica(query, llm, last_entity=None):
    """USA LLM_SMART. Retorna: {tipo, intencion, target_name, keywords, synonyms, donde}"""
    q_lower = query.lower()

    # Bypass para seguridad (comida/niños)
    is_safe_bypass = False
    whitelist = ["helad", "crema", "pelotero", "juego", "niñ", "chic", "infantil"]
    for safe in whitelist:
        if safe in q_lower:
            is_safe_bypass = True

    # Cambiamos v86 a v90 para invalidar cache con el nuevo esquema
    cache_key = f"analysis_v90_{q_lower.strip()}"
    if last_entity:
        cache_key += f"_{last_entity.replace(' ', '_')}"

    cached = await cache.get_json("analysis", cache_key)
    if cached:
        return cached

    template = f"""
    Eres el Router Maestro de un asistente gastronómico de Neuquén.
    Analiza la query: "{query}"
    Contexto previo: Hablábamos de "{last_entity if last_entity else 'Ninguno'}"
    
    Determina la INTENCIÓN y extrae los datos:
    
    1. CATEGORÍAS DE INTENCIÓN:
       - "BLOCK": Insultos o contenido sexual.
       - "STATS": Preguntas de conteo ("cuántos...", "total de...").
       - "SPECIFIC": El usuario pregunta por un LUGAR ESPECÍFICO por su nombre (ej: "Que onda Atila?", "Donde queda El Tío?").
       - "FOLLOWUP": El usuario sigue preguntando sobre el lugar anterior (ej: "y los precios?", "tiene cochera?").
       - "RECOMMENDATION": El usuario busca opciones por tipo, producto o vibra (ej: "bares", "pasteleria en el rio", "lugares con juegos").
    
    2. REGLA DE ORO:
       - Si el usuario menciona una categoría (pastelería, bar, parrilla) junto a una ubicación -> Es RECOMMENDATION.
       - Si el usuario menciona un NOMBRE PROPIO de un local -> Es SPECIFIC.
       - "pasteleria en el rio" -> RECOMMENDATION (Busca el producto, no un local llamado 'Pasteleria').
       - "Que onda Pasteleria Najuian?" -> SPECIFIC.
    
    Responde SOLO JSON:
    {{
        "intencion": "RECOMMENDATION",
        "tipo": "PRODUCTO", 
        "target_name": null,
        "keywords": ["bar"], 
        "synonyms": ["cerveceria", "pub"],
        "donde": "rio"
    }}
    
    Campos:
    - "target_name": Solo si es SPECIFIC, pon el nombre del lugar limpio.
    - "donde": La zona detectada (rio, centro, alto, oeste, etc) o null.
    - "keywords": La palabra clave principal (singular).
    """
    try:
        res = await llm.ainvoke(template)
        clean = res.content.strip().replace("```json", "").replace("```", "")
        data = json.loads(clean)

        if is_safe_bypass and data.get("intencion") == "BLOCK":
            data["intencion"] = "RECOMMENDATION"

        if "synonyms" not in data:
            data["synonyms"] = []

        asyncio.create_task(cache.set_json("analysis", cache_key, data))
        return data
    except:
        return {
            "intencion": "RECOMMENDATION",
            "tipo": "VIBE",
            "keywords": [q_lower],
            "synonyms": [],
        }


def detectar_mencion_exacta(query, df):
    """
    Detecta si el usuario nombró un lugar exacto.
    FIX v7.5: Agregados 'helado', 'heladeria' a la blacklist para evitar matches falsos.
    FIX v7.4: Devuelve el NÚCLEO para permitir menús de opciones.
    """
    if df is None or df.empty:
        global df_lugares
        if df_lugares is not None and not df_lugares.empty:
            df = df_lugares
        else:
            return None
    q_norm = query.lower().strip()

    # HEURISTIC SAFEGUARD: Si la query pide recomendación explícita, NO buscamos match exacto/parcial.
    # Esto previene que "mejores bares en el oeste" matchee con el lugar "Oeste".
    rec_keywords = [
        "mejores",
        "mejor",
        "top",
        "rank",
        "ranking",
        "recomendame",
        "recomenda",
        "busco",
        "lugar para",
        "lugares para",
        "donde comer",
        "donde cenar",
        "donde ir",
        "lugares con",
        "lugares de",
        "bares en",
        "restaurantes en",
        "opciones",
        "opcion",
        "algo con",
        "pasteleria en",
        "pastelerias en",
        "panaderia en",
        "panaderias en",
        # Patrones de zona que indican recomendación
        "en el oeste",
        "en el centro",
        "en el alto",
        "en el norte",
        "en el sur",
        "en el rio",
        "en el río",
        "cerca del rio",
        "cerca del río",
        "cerca del paseo",
        "en la costa",
        "zona rio",
        "zona río",
    ]

    if any(kw in q_norm for kw in rec_keywords):
        print(
            f"[DEBUG] 🛡️ Heuristic blocked Exact Match: Detected recommendation intent in '{query}'",
            flush=True,
        )
        return None

    venue_prefixes = {
        "restaurante",
        "parrilla",
        "bar",
        "confiteria",
        "pizzeria",
        "bodegon",
        "cerveceria",
        "hamburgueseria",
        "heladeria",
        "cafe",
        "bistro",
        "resto",
        "rotiseria",
        "panaderia",
        "pasteleria",
        "sushi",
        "casa",
        "local",
        "negocio",
        "fabrica",
        "elaboracion",
        "reposteria",
    }

    stopwords = {
        "el",
        "la",
        "los",
        "las",
        "de",
        "del",
        "lo",
        "al",
        "y",
        "en",
        "que",
        "qué",
        "tal",
        "como",
        "es",
        "onda",
        "son",
        "para",
        "con",
        "donde",
        "por",
        "sin",
        "sobre",
    }

    # AQUI ESTÁ LA CLAVE: Agregamos helado/heladeria/postres para que no matcheen nombres parciales
    generic_blocklist = {
        "sushi",
        "pizza",
        "pizzas",
        "burger",
        "hamburguesa",
        "hamburguesas",
        "helado",
        "helados",
        "heladeria",
        "heladerias",
        "crema",
        "cremas",
        "birra",
        "cerveza",
        "cervezas",
        "birra",
        "birras",
        "cafe",
        "café",
        "parrilla",
        "pasta",
        "pastas",
        "milanesa",
        "milanesas",
        "ensalada",
        "comida",
        "postre",
        "postres",
        "resto",
        "bar",
        "almuerzo",
        "cena",
        "menu",
        "pasteleria",
        "pastelerias",
        "panaderia",
        "panaderias",
        "reposteria",
        "confiteria",
        "para",
        "con",
        "donde",  # Safety extra
        "tacc",
        "celiaco",
        "celiacos",
        "vegano",
        "vegana",
        "vegetariano",
    }

    nombres = df["restaurante"].unique().tolist()
    nombres.sort(key=len, reverse=True)

    for nombre_real in nombres:
        nombre_lower = nombre_real.lower().strip()

        # 1. Match Exacto Total (Solo si es idéntico)
        if nombre_lower == q_norm:
            return nombre_real

        # Análisis de partes
        parts = nombre_lower.split()
        core_parts = [p for p in parts if re.sub(r"[^\w]", "", p) not in venue_prefixes]
        if not core_parts:
            continue
        core_name = " ".join(core_parts)

        # 2. Match de Núcleo
        if len(core_name) > 3:
            pattern = r"(?<!\w)" + re.escape(core_name) + r"(?!\w)"
            if re.search(pattern, q_norm):
                # Si el núcleo detectado está en la lista negra (ej: "Helados"), LO IGNORAMOS.
                if core_name in generic_blocklist:
                    continue
                return core_name.title()

        # 3. Match Palabra Distintiva
        distinctive_parts = [p for p in core_parts if p not in stopwords]
        if distinctive_parts:
            for part in distinctive_parts:
                clean_part = re.sub(r"[^\w]", "", part)
                if len(clean_part) <= 3:
                    continue
                if clean_part in generic_blocklist:
                    continue

                pattern_dist = r"(?<!\w)" + re.escape(clean_part) + r"(?!\w)"
                if re.search(pattern_dist, q_norm):
                    return clean_part.title()

    return None


# Función eliminada - integrada en analizar_query_semantica


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
        keyword = (
            str(keyword_raw.content).strip().lower().replace('"', "").replace(".", "")
        )

        # 2. Casos especiales de cantidad total
        if keyword in ["total", "todo", "restaurantes", "lugares"]:
            total = df["restaurante"].nunique()
            return (
                f"Tengo registrados un total de **{total}** locales en la base de datos.",
                [],
            )

        if len(keyword) < 3:
            return f"No encontré una categoría clara en '{query}'.", []

        # 3. Filtrado EFICIENTE sobre df_lugares (Solo 936 locales, no 183k reviews)
        global df_lugares
        target_df = df_lugares if df_lugares is not None else df

        # Búsqueda insensible a mayúsculas/acentos simple
        # Para 936 filas es instantáneo
        mask = (
            target_df["restaurante"].str.contains(keyword, case=False, na=False)
            | target_df["categoria"].str.contains(keyword, case=False, na=False)
            | target_df["resumen_reviews"].str.contains(keyword, case=False, na=False)
        )

        matches = target_df[mask]["restaurante"].unique().tolist()
        total_count = len(matches)

        # 5. Generación de Respuesta "Humana" (Mi lógica ganadora)
        if total_count == 0:
            return (
                f"No encontré lugares registrados bajo la categoría **'{keyword}'**.",
                [],
            )

        elif total_count == 1:
            return f"Encontré solo uno: **{matches[0]}**.", matches

        elif total_count <= 10:
            # Si son pocos, los nombramos todos
            lista_str = ", ".join(matches)
            return (
                f"Encontré **{total_count}** lugares de {keyword}: {lista_str}.",
                matches,
            )

        else:
            # Si son muchos, damos el total y ejemplos
            ejemplos = ", ".join(matches[:5])
            return (
                f"¡Un montón! Encontré **{total_count}** lugares de {keyword}. Algunos son: {ejemplos}, entre otros.",
                matches,
            )

    except Exception as e:
        logger.error(f"Error consultar_estadisticas: {e}")
        return "Tuve un problema calculando esa estadística.", []


async def resumir_opiniones_local_gen(
    query_str, df, llm, topic=None, tone="cordial", es_seleccion_directa=False
):
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
    mask_exact = df["restaurante"].str.lower() == q_clean
    if mask_exact.any():
        encontrados = [df[mask_exact].iloc[0]["restaurante"]]
    else:
        if es_seleccion_directa:
            if mask_exact.any():
                encontrados = [df[mask_exact].iloc[0]["restaurante"]]
        else:
            mask = (
                df["restaurante"]
                .str.lower()
                .str.contains(q_clean, na=False, regex=False)
            )
            candidatos = df[mask]["restaurante"].unique().tolist()

            if len(candidatos) == 1:
                encontrados = candidatos
            elif len(candidatos) > 1:
                encontrados = candidatos
                encontrados.sort()
                # Menú de opciones
                labels = []
                for r in encontrados:
                    mask_r = df["restaurante"] == r
                    rowr = df[mask_r].iloc[0]
                    ubi = (
                        safe_str(rowr.get("direccion"))
                        or safe_str(rowr.get("zona"))
                        or "Ubicación desconocida"
                    )
                    labels.append(f"**{r}** ({ubi})")
                lista_txt = "\n".join([f"{i+1}. {lbl}" for i, lbl in enumerate(labels)])
                yield {
                    "type": "menu",
                    "text": f"Encontré varios lugares con ese nombre. ¿Cuál decís?\n\n{lista_txt}\n\n*(Escribí el número)*",
                    "options": encontrados,
                    "labels": [lbl.replace("**", "") for lbl in labels],
                }
                return

    # 3. Si no encontró nada
    if not encontrados:
        yield {
            "type": "error",
            "text": f"No tengo info de **{query_str}**. Probá con otro nombre.",
        }
        return

    # 4. Chequeo de Caché
    restaurante = encontrados[0]
    yield {"type": "meta", "restaurante": restaurante, "found": True}

    cache_key = (
        f"{restaurante}_{topic}_{sanitize_tone(tone)}"
        if topic
        else f"{restaurante}__{sanitize_tone(tone)}"
    )
    cached_text = await cache.get_json("resumen_texto", cache_key)
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
    row_data = df[df["restaurante"] == restaurante].iloc[0]

    zona_str = safe_str(row_data.get("zona")).lower()
    dir_str = safe_str(row_data.get("direccion")).lower()

    # Lógica para determinar la ciudad correcta
    ubicacion_real = "Neuquén Capital"  # Default
    if "cipolletti" in zona_str or "cipolletti" in dir_str:
        ubicacion_real = "Cipolletti, Río Negro"
    elif "plottier" in zona_str or "plottier" in dir_str:
        ubicacion_real = "Plottier, Neuquén"
    elif "centenario" in zona_str or "centenario" in dir_str:
        ubicacion_real = "Centenario, Neuquén"
    elif safe_str(
        row_data.get("zona")
    ):  # Si tiene zona pero no es otra ciudad (ej: "Alta Barda")
        ubicacion_real = f"Neuquén Capital (Zona {safe_str(row_data.get('zona'))})"

    sorted_reviews = rankear_reviews_por_topico(
        df[df["restaurante"] == restaurante], topic
    )
    reviews_txt = "\n".join(
        [safe_str(r.get("texto"))[:200] for _, r in sorted_reviews.head(10).iterrows()]
    )

    contexto_tema = (
        f"El usuario pregunta específicamente sobre: '{topic}'. Resalta eso."
        if topic
        else ""
    )
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
            "rat": safe_float(row_data.get("rating_gral")),
            "revs": reviews_txt,
        }

        # Stream dispatch
        async for token in astream_buffer(
            chain, args, cache_key=cache_key, cache_instance=cache
        ):
            yield {"type": "token", "content": token}

    except Exception as e:
        yield {"type": "error", "text": "No pude generar el resumen."}


async def resumir_opiniones_local(
    query_str, df, llm, topic=None, tone="cordial", es_seleccion_directa=False
):
    """
    Wrapper legacy for compatibility with non-streaming callers.
    """
    full_text = ""
    restaurante = None
    options = None

    async for event in resumir_opiniones_local_gen(
        query_str, df, llm, topic, tone, es_seleccion_directa
    ):
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


async def responder_followup_gen(restaurante, query, df, llm, tone="cordial"):
    """
    Generates a targeted answer to a follow-up question based on reviews.
    Yields tokens.
    """
    # 1. Validation
    if not restaurante:
        yield {
            "type": "token",
            "content": "No tengo un lugar seleccionado para responderte.",
        }
        return

    mask = df["restaurante"] == restaurante
    if not mask.any():
        yield {"type": "token", "content": f"No encuentro info de {restaurante}."}
        return

    # 2. Get reviews and filter by topic/relevance
    # reusing logic from rankear_reviews_por_topico
    sorted_reviews = rankear_reviews_por_topico(df[mask], query)

    # Take top 15 reviews to have enough context
    reviews_txt = "\n".join(
        [
            f"- {safe_str(r.get('texto'))[:300]}"
            for _, r in sorted_reviews.head(15).iterrows()
        ]
    )

    if not reviews_txt.strip():
        yield {
            "type": "token",
            "content": f"No encontré reseñas específicas sobre '{query}' para {restaurante}.",
        }
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
        chain = ChatPromptTemplate.from_template(prompt) | llm_mini | StrOutputParser()
        args = {}
        async for token in astream_buffer(chain, args):
            yield {"type": "token", "content": token}
    except Exception as e:
        logger.error(f"Error responder_followup: {e}")
        yield {"type": "token", "content": "Tuve un error procesando tu pregunta."}
def obtener_evidencia_para_juez(candidatos, df_lugares_ref, keywords):
    """LAZY v8: Usa resumen_reviews de df_lugares (936 filas) en vez de 155k reviews."""
    texto_validacion = ""
    for local in candidatos[:10]:
        if local not in df_lugares_ref.index:
            continue

        row = df_lugares_ref.loc[local]
        if isinstance(row, pd.DataFrame): row = row.iloc[0]

        rating = safe_float(row.get("rating_gral", 0))
        total_reviews = safe_int(row.get("total_reviews_google", 0))
        resumen = safe_str(row.get("resumen_reviews", ""))

        if not resumen or len(resumen) < 20:
            resumen = f"Restaurante en Neuquén con {total_reviews} reseñas."

        # Buscar evidencia en el resumen
        snippet = resumen[:700].replace("\n", " ")
        prefix = "RESUMEN:"

        if keywords:
            pattern = "|".join([re.escape(k) for k in keywords])
            if re.search(pattern, resumen, re.IGNORECASE):
                prefix = "EVIDENCIA:"

        texto_validacion += f'- LOCAL: {local} (⭐{rating} - {total_reviews} reseñas)\n  {prefix} "...{snippet}..."\n\n'
    return texto_validacion

async def verificar_candidatos_con_llm(candidatos, df_lugares_ref, query, llm):
    """
    JUEZ SEMÁNTICO (V4 - LAZY):
    Usa df_lugares.resumen_reviews en vez de reviews individuales.
    """
    if not candidatos:
        return []

    stop_short = ["que", "los", "las", "con", "para", "donde", "hay", "lugar"]
    words = query.lower().split()
    keywords = [w for w in words if len(w) > 3 and w not in stop_short]

    # Evidencia desde resumen_reviews (936 filas, instantáneo)
    texto_validacion = await asyncio.to_thread(obtener_evidencia_para_juez, candidatos, df_lugares_ref, keywords)

    prompt = f"""
    Eres un validador de catálogo, NO un crítico. Query: "{query}"
    
    TAREA: Determinar si el local OFRECE lo que el usuario busca.
    
    REGLA DE ORO: SI EL LOCAL TIENE EL PRODUCTO (o es de la categoría), APRUÉBALO. No importa si la reseña dice que es "feo", "industrial", "caro" o "lento". 
    
    MOTIVOS VÁLIDOS DE RECHAZO (Y SOLO ESTOS):
    1. CATEGORÍA TOTALMENTE ERRÓNEA: El usuario busca "Pizza" y el local es una "Heladería" que no vende comida.
    2. REQUISITO EXCLUYENTE INCUMPLIDO: Busca "Sin TACC", "Vegano" o "Pet Friendly" y el local NO lo es.
    3. CERRADO DEFINITIVAMENTE: El local no existe más.
    
    ESTÁ PROHIBIDO RECHAZAR POR:
    - Calidad/Sabor: "La milanesa es dura", "el rebozado es industrial", "no es rico". (EL USUARIO DECIDIRÁ SI LE GUSTA).
    - Servicio/Higiene: "Tardaron mucho", "estaba sucio", "mala atención". (NO RECHACES POR ESTO).
    - Foco del local: Si el usuario busca "milanesas" y una "Marisquería" o "Cervecería" las ofrece en su menú, DEBES APROBARLO aunque no sea su especialidad.
    
    Si tienes la más mínima duda de si lo venden, ¡APRUEBA! Es mejor mostrar de más que de menos.
    
    CANDIDATOS:
    {texto_validacion}
    
    Responde JSON:
    {{
        "aprobados": ["Nombres de locales que pasan"],
        "rechazados": {{"Nombre del Local": "Solo si es un error grosero de categoría o está cerrado"}}
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


def aplicar_filtro_zona(candidatos, df, zona_buscada, verbose=True):
    """
    Filtra la lista de nombres de restaurantes según si coinciden con la zona buscada.
    Optimizado: Usa df_lugares (900 filas) indexado para filtrar miles de candidatos en ms.
    """
    global df_lugares
    if not zona_buscada or not candidatos:
        return candidatos

    z_clean = zona_buscada.lower().strip()
    search_terms = [z_clean]

    # Si busca "rio", "paseo" o "costa", ampliamos la búsqueda
    if any(x in z_clean for x in ["rio", "río", "paseo", "costa", "limay", "isla"]):
        search_terms.extend(["rio", "río", "limay", "balneario", "paseo", "costa", "costanera", "ribera", "isla", "132", "isla 132"])

    if "alto" in z_clean or "norte" in z_clean:
        search_terms.extend(["alto", "norte", "barda", "parque industrial", "terrazas"])

    if "centro" in z_clean:
        search_terms.extend(["centro", "bajo"])

    if "oeste" in z_clean:
        search_terms.extend(["oeste", "aeropuerto", "canal"])

    candidatos_filtrados = []
    
    # Lookup metadata for ALL candidates at once O(1) inside loop
    valid_cands = [c for c in candidatos if c in df_lugares.index]
    if not valid_cands:
        return []
        
    meta_df = df_lugares.loc[valid_cands]
    if isinstance(meta_df, pd.Series): meta_df = meta_df.to_frame().T

    for local, row in meta_df.iterrows():
        geo_data = f"{safe_str(row.get('zona'))} {safe_str(row.get('barrio'))}".lower()
        
        # EXCLUSIÓN: "Río Negro" provincia
        if "río negro" in geo_data or "rio negro" in geo_data:
            geo_data_clean = geo_data.replace("río negro", "").replace("rio negro", "")
            if not any(term in geo_data_clean for term in search_terms):
                continue

        match = False
        for term in search_terms:
            if re.search(r"\b" + re.escape(term) + r"\b", geo_data):
                match = True
                break
        
        if match:
            candidatos_filtrados.append(local)
    
    return candidatos_filtrados


async def resolver_target_con_llm(query, last_entity, llm):
    """
    Decide si el usuario sigue hablando del 'last_entity' o cambia a un tema nuesvo.
    Devuelve:
    - nombre de la entidad (last_entity o nueva)
    - "NONE" si no hay entidad clara
    """
    if not last_entity:
        return "NONE"

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
        text = res.content.strip().replace('"', "").replace(".", "")
        # Basic cleanup
        if text.upper() == "NONE":
            return "NONE"
        return text
    except:
        return last_entity  # Conservative fallback


def obtener_categorias_desde_keywords(keywords, synonyms):
    """
    Retorna una lista de categorías oficiales que matchean con las keywords/sinónimos.
    """
    categorias_encontradas = []
    all_terms = [k.lower().strip() for k in (keywords + synonyms)]

    for term in all_terms:
        if term in KEYWORD_TO_CATEGORIES:
            categorias_encontradas.extend(KEYWORD_TO_CATEGORIES[term])

    return list(set(categorias_encontradas))


def procesar_recomendacion_pesado(
    candidatos_crudos, df_lugares_ref, keywords, synonyms, es_query_generica, zona_detectada
):
    """
    Función síncrona para ejecutar las operaciones pesadas de Pandas/CPU fuera del event loop.
    LAZY v8: Ya no usa df de reviews (155k). Solo usa df_lugares (936 filas).
    Retorna (candidatos_a_verificar, grupo_alta_relevancia).
    """
    # 1. Filtro de zona inicial
    if zona_detectada:
        candidatos_crudos = aplicar_filtro_zona(candidatos_crudos, None, zona_detectada, verbose=False)

    # 2. HYBRID INJECTION (ahora sobre resumen_reviews de df_lugares — 936 filas)
    if keywords and "resumen_reviews" in df_lugares_ref.columns:
        df_para_buscar = df_lugares_ref
        if zona_detectada:
            locales_en_zona = aplicar_filtro_zona(df_lugares_ref.index.tolist(), None, zona_detectada, verbose=False)
            valid_locales = [l for l in locales_en_zona if l in df_lugares_ref.index]
            if valid_locales:
                df_para_buscar = df_lugares_ref.loc[valid_locales]

        for kw in keywords:
            if len(kw) < 4: continue
            mask = df_para_buscar["resumen_reviews"].str.contains(re.escape(kw), case=False, na=False)
            if mask.any():
                matches = df_para_buscar[mask].index.tolist()
                for cand in matches[:10]:
                    if cand not in candidatos_crudos:
                        candidatos_crudos.append(cand)

    # 3. HARD FILTER (reviews >= 30)
    candidatos_limpios = []
    for local in candidatos_crudos:
        if local in df_lugares_ref.index:
            row = df_lugares_ref.loc[local]
            if isinstance(row, pd.DataFrame): row = row.iloc[0]
            if safe_int(row.get("total_reviews_google", 0)) >= 30:
                candidatos_limpios.append(local)
    candidatos_crudos = candidatos_limpios

    # 4. RELEVANCIA Y SORTING (ahora sobre resumen_reviews)
    filtro_terms = [t.lower() for t in (set(keywords) | set(synonyms or [])) if len(t) > 3]
    grupo_alta = []
    grupo_baja = []

    def calc_score(nombre):
        if nombre not in df_lugares_ref.index: return 0
        r = df_lugares_ref.loc[nombre]
        if isinstance(r, pd.DataFrame): r = r.iloc[0]
        return safe_float(r.get("rating_gral")) + (math.log10(safe_int(r.get("total_reviews_google")) + 1) * 2.7)

    if es_query_generica:
        grupo_alta = candidatos_crudos
    elif filtro_terms and "resumen_reviews" in df_lugares_ref.columns:
        patron = "|".join([re.escape(t) for t in filtro_terms])
        for local in candidatos_crudos:
            if local in df_lugares_ref.index:
                resumen = safe_str(df_lugares_ref.loc[local].get("resumen_reviews", "")).lower()
                if re.search(patron, resumen):
                    grupo_alta.append(local)
                    continue
            grupo_baja.append(local)
    else:
        grupo_alta = candidatos_crudos

    grupo_alta.sort(key=calc_score, reverse=True)
    grupo_baja.sort(key=calc_score, reverse=True)

    candidatos_verif = (grupo_alta[:12] + grupo_baja[:12])[:12]
    return candidatos_verif, grupo_alta



async def procesar_consulta_gen(
    query, df, vectorstore, llm_mini, llm_smart, ctx=None, user_ip=None
):
    """
    Generator that handles the chat logic.
    Yields:
      - {"type": "token", "content": "..."}
      - {"type": "meta", "mode": "...", "cards": [...], "locs": [...], "pending": ..., "intent": "..."}
      - {"type": "debug", "message": "..."}
      - {"type": "error", "message": "..."}
    """
    if user_ip and await cache.get_value(f"ban:{user_ip}"):
        raise HTTPException(status_code=403, detail="Baneado por exceso de requests")
    if ctx is None:
        ctx = {}
    t_start = time.time()

    # 🐛 DEBUG DE ENTRADA
    yield {"type": "debug", "message": f"🟢 ENTRADA A CEREBRO | Query: '{query}'"}

    tone = sanitize_tone(ctx.get("tone"))

    # ==========================================
    # 1. CAPA DE SEGURIDAD (NUCLEAR)
    # ==========================================
    if user_ip and await cache.get_value(f"ban:{user_ip}"):
        yield {"type": "meta", "mode": "blocked"}
        yield {"type": "token", "content": "⛔ Sistema bloqueado."}
        return

    strikes = ctx.get("strikes", 0)
    if strikes >= 5:
        yield {"type": "meta", "mode": "blocked"}
        yield {"type": "token", "content": "⛔ Bloqueado."}
        return

    if check_keyword_ban(query):
        ctx["strikes"] = strikes + 1
        yield {"type": "meta", "mode": "rag"}  # Or 'blocked'? Logic said 'rag' before.
        yield {"type": "token", "content": f"Epa, esa búsqueda no va. ({strikes+1}/5)"}
        return

    # ==========================================
    # 2. CONTEXTO NUMÉRICO (MENÚS)
    # ==========================================
    if "pending_options" in ctx and query.strip().isdigit():
        num = int(query.strip())
        pending = ctx["pending_options"]
        opciones = pending.get("options", [])
        if 1 <= num <= len(opciones):
            seleccion = opciones[num - 1]
            ctx["last_entity"] = seleccion
            original_topic = ctx.get("original_query", seleccion)

            # Call generator
            # Need to capture nombre_real from meta event
            nombre_real = None
            async for event in resumir_opiniones_local_gen(
                seleccion, df, llm_mini, original_topic, tone, True
            ):
                if event["type"] == "meta":
                    if "restaurante" in event:
                        nombre_real = event["restaurante"]
                    # Propagate meta if relevant? internal meta might not match outer meta structure.
                elif event["type"] == "token":
                    yield event
                elif event["type"] == "error":
                    yield {"type": "token", "content": event["text"]}

            if nombre_real:
                cards = await obtener_restaurant_cards(
                    [nombre_real], df, llm_mini, original_topic, tone
                )
                locs = obtener_coordenadas([nombre_real], df)
                # Yield meta at the end or when available
                yield {"type": "meta", "mode": "resumen", "cards": cards, "locs": locs}
            return

        yield {"type": "meta", "mode": "resumen", "pending": pending}
        yield {"type": "token", "content": f"Elegí entre 1 y {len(opciones)}"}
        return

    # ==========================================
    # 3. SMART ROUTING (EL CEREBRO V9 - LLM MASTER ROUTER)
    # ==========================================
    last_ent = ctx.get("last_entity")

    # === PARALLEL LAUNCH: Intent Analysis + Vector Search ===
    # Lanzamos ambos en paralelo porque el vector search no depende del intent.
    # Ahorro: ~2s (el intent LLM ya no bloquea al embedding).
    
    async def _cached_vector_search(query_text):
        """Vector search con caché Redis de 7 días."""
        if not vectorstore:
            return []
        cache_key = query_text.lower().strip()[:100]  # normalizar key
        cached = await cache.get_json("vsearch", cache_key)
        if cached:
            print(f"[TIMING] Vector Search HIT (Redis cache)", flush=True)
            return cached
        t0 = time.time()
        try:
            docs = await vectorstore.asimilarity_search(query_text, k=50)
        except Exception:
            # Fallback a sync si async no está disponible
            docs = vectorstore.similarity_search(query_text, k=50)
        seen = set()
        result = []
        for d in docs:
            nom = d.metadata.get("nombre")
            if nom and nom not in seen:
                seen.add(nom)
                result.append(nom)
        t1 = time.time()
        print(f"[TIMING] Vector Search took {t1-t0:.2f}s", flush=True)
        asyncio.create_task(cache.set_json("vsearch", cache_key, result, expire=604800))  # 7 días
        return result
    
    # Lanzar ambos en paralelo
    intent_task = asyncio.create_task(analizar_query_semantica(query, llm_smart, last_entity=last_ent))
    vec_task = asyncio.create_task(_cached_vector_search(query))
    
    # Esperamos solo el intent ahora (vec_task se espera después si hace falta)
    analisis = await intent_task
    intencion = analisis.get("intencion", "RECOMMENDATION")

    # Extraer datos del análisis para los caminos posteriores
    keywords = analisis.get("keywords", [])
    synonyms = analisis.get("synonyms", [])
    zona_detectada = analisis.get("donde")
    target_forced = analisis.get(
        "target_name"
    )  # Si es SPECIFIC, el LLM nos da el nombre

    # Ajuste de compatibilidad de nombres de intención
    if intencion == "SPECIFIC":
        intencion = "SPECIFIC_INFO"

    yield {
        "type": "debug",
        "message": f"🧠 INTENCIÓN: {intencion} | Zona: {zona_detectada}",
    }

    # --- CAMINO A: ESTADÍSTICAS ---
    if intencion == "STATS":
        # SAFETY NET: If we have a last_entity, avoid entering STATS for ambiguous follow-ups
        # like "precios?", "horarios?", "y la carta?".
        # Only allow STATS if the user explicitly asks for magnitude/quantity.
        is_explicit_stats = any(
            x in query.lower()
            for x in [
                "total",
                "cuantos",
                "cuántos",
                "cantidad",
                "numero de",
                "número de",
                "hay ",
            ]
        )

        if ctx.get("last_entity") and not is_explicit_stats:
            print(
                f"[DEBUG] 🔄 Re-routing ambiguous STATS '{query}' to SPECIFIC because active context exists",
                flush=True,
            )
            intencion = "SPECIFIC_INFO"
        else:
            if "last_entity" in ctx:
                del ctx["last_entity"]

            resp, locales = await consultar_estadisticas(query, df, llm_mini)
            cards = obtener_restaurant_cards_simple(locales, df)
        locs = obtener_coordenadas(locales, df)

        yield {"type": "meta", "mode": "estadisticas", "cards": cards, "locs": locs}
        yield {"type": "token", "content": resp}
        return

    # --- CAMINO AB: FOLLOWUP (PREGUNTA ESPECÍFICA DE SEGUIMIENTO) ---
    if intencion == "FOLLOWUP":
        target = ctx.get("last_entity")

        if not target:
            # Fallback: Router says yes, but we have no context.
            yield {
                "type": "token",
                "content": "Perdón, me perdí. ¿De qué lugar estábamos hablando?",
            }
            yield {"type": "debug", "message": "⚠️ Intent FOLLOWUP sin last_entity"}
        else:
            # Streaming answer
            yield {"type": "debug", "message": f"🔄 FOLLOWUP sobre '{target}'"}
            # Optional: Yield meta to keep UI context?
            # yield {"type": "meta", "mode": "followup", "restaurante": target}

            async for event in responder_followup_gen(
                target, query, df, llm_mini, tone
            ):
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

        # PRIORIDAD 1: Usar el target_name que el LLM detectó (es el más inteligente)
        if target_forced:
            # Normalizamos contra el DF por si el LLM typoed (aunque Atila -> Atila suele venir bien)
            match_exacto = detectar_mencion_exacta(target_forced, df)
            target = match_exacto if match_exacto else target_forced
            print(
                f"[DEBUG] 🎯 Target desde LLM: '{target_forced}' -> Resolvido: '{target}'",
                flush=True,
            )
        else:
            # FALLBACK: Si el LLM dijo SPECIFIC pero no dio nombre (raro), o para mayor seguridad:
            nuevo_candidato = detectar_mencion_exacta(query, df)
            if nuevo_candidato:
                target = nuevo_candidato
            else:
                # Fuzzy RapidFuzz
                candidates = df["restaurante"].unique().tolist()
                match = process.extractOne(
                    query, candidates, scorer=fuzz.token_set_ratio, score_cutoff=85
                )
                if match:
                    target = match[0]
                elif ctx.get("last_entity"):
                    target = await resolver_target_con_llm(
                        query, ctx.get("last_entity"), llm_mini
                    )
                else:
                    target = query

        if target:
            match_exists = True  # Assume true if we resolved it or came from context

            if match_exists:
                es_solo_navegacion = len(query.strip()) <= len(target.strip()) + 5
                topic_actual = None if es_solo_navegacion else query

                if "original_query" in ctx:
                    del ctx["original_query"]
                if topic_actual:
                    ctx["original_query"] = topic_actual

                # Generator Call
                found_valid_content = False
                temp_error_msg = None
                options_found = None

                async for event in resumir_opiniones_local_gen(
                    target, df, llm_mini, topic=topic_actual, tone=tone
                ):
                    if event["type"] == "token":
                        yield event
                        found_valid_content = True
                    elif event["type"] == "meta":
                        if "restaurante" in event:
                            nombre_final = event["restaurante"]
                        # If found, propagate meta
                        # yield event # Or wait? stream reader handles meta.
                        # resumir_opiniones_local_gen yields token content.
                        # We should yield meta too for context update?
                        # The original code only captured nombre_final and yielded meta later?
                        # No, detecting loop logic:
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
                    yield {
                        "type": "meta",
                        "mode": "resumen",
                        "pending": {"options": options_found},
                    }
                    return

                if found_valid_content:
                    if nombre_final:
                        ctx["last_entity"] = nombre_final
                        cards = await obtener_restaurant_cards(
                            [nombre_final],
                            df,
                            llm_mini,
                            query_context=topic_actual,
                            tone=tone,
                        )
                        locs = obtener_coordenadas([nombre_final], df)
                        yield {
                            "type": "meta",
                            "mode": "resumen",
                            "cards": cards,
                            "locs": locs,
                        }
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
        vars_to_kill = ["last_entity", "original_query", "pending_options"]
        for var in vars_to_kill:
            if var in ctx:
                del ctx[var]

        try:
            if df_lugares is None or df_lugares.empty:
                yield {
                    "type": "meta",
                    "mode": "rag",
                    "cards": [],
                    "locs": [],
                    "zona": zona_detectada,
                }
                yield {
                    "type": "token",
                    "content": "Aún no tengo información cargada en mi base de datos para responder.",
                }
                return
            if analisis.get("intencion") == "BLOCK":
                ctx["strikes"] = strikes + 1
                yield {"type": "meta", "mode": "rag"}
                yield {
                    "type": "token",
                    "content": f"Epa, esa búsqueda no va. ({strikes+1}/5)",
                }
                return

            print(f"[DEBUG] 📍 Zona detectada por LLM: '{zona_detectada}'", flush=True)

            # --- DETECCIÓN DE CATEGORÍAS GENÉRICAS ---
            categorias_detectadas = obtener_categorias_desde_keywords(
                keywords, synonyms
            )

            # Es genérica si al menos una keyword es categoría y NO hay keywords extra específicas
            keywords_extra = [
                k for k in keywords if k.lower().strip() not in KEYWORD_TO_CATEGORIES
            ]
            es_query_generica = (
                len(categorias_detectadas) > 0 and len(keywords_extra) == 0
            )

            # Si no hay keywords detectadas (query rara), no es genérica
            if not keywords:
                es_query_generica = False

            # FALLBACK: Detección heurística de zona si LLM no la detectó
            if not zona_detectada:
                zona_patterns = [
                    (r"\ben\s+(?:el|la)?\s*(rio|río)", "rio"),
                    (r"\ben\s+(?:el|la)?\s*(centro)", "centro"),
                    (r"\ben\s+(?:el|la)?\s*(alto)", "alto"),
                    (r"\ben\s+(?:el|la)?\s*(oeste)", "oeste"),
                    (r"\ben\s+(?:el|la)?\s*(este)", "este"),
                    (r"\b(paseo\s*(?:de\s*la)?\s*costa)", "rio"),
                    (r"\b(costanera|ribera)", "rio"),
                    (
                        r"\bzona\s+(rio|río|centro|alto|oeste|este)",
                        None,
                    ),  # Captura directa
                ]
                q_lower = query.lower()
                for pattern, default_zone in zona_patterns:
                    match = re.search(pattern, q_lower)
                    if match:
                        zona_detectada = (
                            default_zone if default_zone else match.group(1)
                        )
                        print(
                            f"[DEBUG] 📍 Zona detectada por REGEX fallback: '{zona_detectada}'",
                            flush=True,
                        )
                        break

            candidatos_crudos = []
            skip_juez = False

            # ESTRATEGIA A: MODO GENÉRICO (Búsqueda por Categoría)
            if es_query_generica:
                print(f"[DEBUG] 🚀 MODO GENÉRICO detectado para categorias: {categorias_detectadas[:3]}...", flush=True)
                if "categoria" in df_lugares.columns:
                    mask_cat = df_lugares["categoria"].isin(categorias_detectadas)
                    if mask_cat.any():
                        candidatos_crudos = df_lugares[mask_cat]["restaurante"].unique().tolist()
                        print(f"[DEBUG] 🏷️ Candidatos encontrados por categoría: {len(candidatos_crudos)}", flush=True)
                        skip_juez = True
                else:
                    logger.warning("⚠️ Columna 'categoria' no encontrada en df_lugares.")

            # ESTRATEGIA B: MODO ESPECÍFICO (Vector Search + Hybrid)
            if not candidatos_crudos:
                candidatos_crudos = await vec_task

            # --- OPERACIONES PESADAS (OFFLOAD TO THREAD) ---
            # Filtro de zona, Hybrid Injection, Hard Filter y Ranking se hacen en un thread aparte.
            t_pesado_start = time.time()
            candidatos_a_verificar, grupo_alta_relevancia = await asyncio.to_thread(
                procesar_recomendacion_pesado,
                candidatos_crudos,
                df_lugares,
                keywords,
                synonyms,
                es_query_generica,
                zona_detectada
            )
            print(f"[TIMING] Heavy Pandas operations took {time.time() - t_pesado_start:.2f}s (in thread)", flush=True)

            # 5. EL JUEZ LLM (Verificación de Contexto)
            query_para_juez = query
            if zona_detectada:
                ubicacion_patterns = [
                    r"\s*en\s+(?:el|la)\s+" + re.escape(zona_detectada),
                    r"\s*zona\s+" + re.escape(zona_detectada),
                    r"\s*del?\s+" + re.escape(zona_detectada),
                    r"\s*cerca\s+del?\s+" + re.escape(zona_detectada),
                ]
                for pattern in ubicacion_patterns:
                    query_para_juez = re.sub(pattern, "", query_para_juez, flags=re.IGNORECASE)
                query_para_juez = query_para_juez.strip()
                print(f"[DEBUG] 🧹 Query para Juez (sin ubicación): '{query_para_juez}'", flush=True)

            if skip_juez:
                locales_verificados = candidatos_a_verificar
                print(f"[DEBUG] ⏩ Saltando Juez LLM (Modo Genérico activo)", flush=True)
            else:
                t0 = time.time()
                locales_verificados = await verificar_candidatos_con_llm(
                    candidatos_a_verificar, df_lugares, query_para_juez, llm_mini
                )
                t1 = time.time()
                print(f"[TIMING] Juez LLM took {t1-t0:.2f}s", flush=True)

            # DEBUG: Ver qué aprobó el juez
            print(
                f"[DEBUG] ⚖️ Juez LLM aprobó: {len(locales_verificados)} de {len(candidatos_a_verificar)}",
                flush=True,
            )
            print(f"[DEBUG] ⚖️ Aprobados: {locales_verificados}", flush=True)

            # Identificar rechazados explícitamente para no mostrarlos en 'relacionados'
            locales_rechazados = set(candidatos_a_verificar) - set(locales_verificados)

            # Separar EXACTOS (aprobados por juez) y RELACIONADOS (alta relevancia no aprobados)
            exactos = locales_verificados[:3]  # Máximo 3 exactos (antes 6)

            # Relacionados: tomamos de alta relevancia, excluyendo los que ya son exactos Y los que fueron RECHAZADOS por el juez
            relacionados = [
                loc
                for loc in grupo_alta_relevancia
                if loc not in exactos and loc not in locales_rechazados
            ][
                :2
            ]  # Máximo 2 relacionados (antes 4)

            print(f"[DEBUG] 🎯 Exactos: {exactos}", flush=True)
            print(f"[DEBUG] 🚫 Rechazados filtrados: {locales_rechazados}", flush=True)
            print(f"[DEBUG] 🔗 Relacionados: {relacionados}", flush=True)

            # 6. RANKING FINAL (Final sort by rating and review count)
            def get_score(n):
                if n not in df_lugares.index: return 0
                r = df_lugares.loc[n]
                if isinstance(r, pd.DataFrame): r = r.iloc[0]
                return safe_float(r.get("rating_gral")) + (math.log10(safe_int(r.get("total_reviews_google")) + 1) * 2.7)

            exactos.sort(key=get_score, reverse=True)
            relacionados.sort(key=get_score, reverse=True)

            if not exactos and not relacionados:
                yield {
                    "type": "meta",
                    "mode": "rag",
                    "cards": [],
                    "locs": [],
                    "zona": zona_detectada,
                }
                yield {
                    "type": "token",
                    "content": "No encontré lugares que cumplan con ese requisito específico.",
                }
                return

            # 6. GENERACIÓN PARALELA
            t2 = time.time()
            todos_los_locales = exactos + relacionados  # Up to 10 cards total

            # Helper para el contexto RÁPIDO — usa solo df_lugares.resumen_reviews
            def construir_contexto_rapido(nombres, df_lugares_ref):
                contexto = ""
                for nom in nombres:
                    if nom not in df_lugares_ref.index:
                        continue
                    row_l = df_lugares_ref.loc[nom]
                    if isinstance(row_l, pd.DataFrame): row_l = row_l.iloc[0]

                    rat = safe_float(row_l.get("rating_gral"))
                    revs = safe_int(row_l.get("total_reviews_google"))
                    resumen_ia = safe_str(row_l.get("resumen_reviews"))
                    texto_final = resumen_ia[:600].replace("\n", " ") if resumen_ia and len(resumen_ia) > 30 else "Restaurante popular en Neuquén."

                    contexto += f"- {nom} ({rat}⭐, {revs} res): {texto_final}...\n"
                return contexto

            detalles_exactos = construir_contexto_rapido(exactos, df_lugares)
            detalles_relacionados = construir_contexto_rapido(relacionados, df_lugares)

            # Build prompt FIRST (cards deferred to avoid API contention)
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
                    "4. IMPORTANTE: Inicia cada recomendación con el formato estricto: '**Nombre del Lugar** - (⭐ Rating, N reseñas):' seguido de tu descripción."
                )
            elif exactos:
                prompt_rag = (
                    f"{tone_system_instruction(tone)}\n"
                    f"SITUACIÓN: El usuario buscó '{query}'.\n"
                    f"Resultados:\n{detalles_exactos}\n\n"
                    f"INSTRUCCIONES:\n"
                    "1. Confirma que encontraste lo que buscaba.\n"
                    "2. Describelos usando la info provista.\n"
                    "3. IMPORTANTE: Inicia cada recomendación con el formato estricto: '**Nombre del Lugar** - (⭐ Rating, N reseñas):' seguido de tu descripción."
                )
            else:
                prompt_rag = (
                    f"{tone_system_instruction(tone)}\n"
                    f"SITUACIÓN: El usuario buscó '{query}'.\n"
                    f"Solo encontré RELACIONADOS:\n{detalles_relacionados}\n\n"
                    f"INSTRUCCIONES:\n"
                    "1. Aclara que no encontraste match exacto.\n"
                    "2. Ofrece estos relacionados.\n"
                    "3. IMPORTANTE: Inicia cada recomendación con el formato estricto: '**Nombre del Lugar** - (⭐ Rating, N reseñas):' seguido de tu descripción."
                )

            t_stream_start = time.time()
            print(
                f"[TIMING] Pre-stream setup (Parallel) took {t_stream_start - t_start:.2f}s total. Starting stream...",
                flush=True,
            )

            # STREAMING THE RAG RESPONSE
            # Cards deferred: start AFTER first token to avoid OpenAI API contention
            # (11 concurrent calls caused 32s throttle delay)
            card_task = None
            async for token in astream_buffer(llm_mini, prompt_rag):
                if card_task is None:
                    # First token flowed → NOW start card generation in parallel
                    print(f"[TIMING] First token at {time.time() - t_start:.2f}s. Starting card gen...", flush=True)
                    card_task = asyncio.create_task(
                        obtener_restaurant_cards(
                            todos_los_locales,
                            df_lugares,
                            llm_mini,
                            query,
                            tone,
                            strict_mode=False,
                            keywords_list=keywords,
                            synonyms_list=synonyms,
                        )
                    )
                yield {"type": "token", "content": token}

            # AWAIT CARDS AND YIELD
            print(f"[TIMING] Text stream finished. Waiting for cards...", flush=True)
            cards = await card_task if card_task else []
            t3 = time.time()
            print(
                f"[TIMING] Card Gen finished at {t3 - t_start:.2f}s total (Latencia oculta)",
                flush=True,
            )

            nombres_finales = [c.nombre for c in cards]
            locs = obtener_coordenadas(nombres_finales, df)

            # Yield Metadata at the END
            yield {
                "type": "meta",
                "mode": "rag",
                "cards": cards,
                "locs": locs,
                "zona": zona_detectada,
            }
            return

        except Exception as e:
            logger.error(f"Error RAG: {e}")
            yield {"type": "meta", "mode": "rag"}
            yield {"type": "token", "content": "Tuve un problema técnico buscando eso."}
            return

    # --- CAMINO D: GENERAL ---
    if intencion == "BLOCK":
        ctx["strikes"] = strikes + 1
        yield {"type": "meta", "mode": "general"}
        yield {
            "type": "token",
            "content": "Epa, bajemos un cambio. Mantené el respeto, estoy acá para ayudar. (Strike sumado)",
        }
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
        yield {
            "type": "token",
            "content": "¡Buenas! ¿En qué te puedo ayudar para comer hoy?",
        }


async def procesar_consulta(
    query, df, vectorstore, llm_mini, llm_smart, ctx=None, user_ip=None
):
    """
    Wrapper legacy that consumes the generator and returns the full response tuple.
    Returns: (resp, mode, pend, locs, cards, det)
    """
    full_text = ""
    mode = "general"
    cards = []
    locs = []
    pend = None
    det = ""  # Not really used in gen yet, but kept for signature

    async for event in procesar_consulta_gen(
        query, df, vectorstore, llm_mini, llm_smart, ctx, user_ip
    ):
        if event["type"] == "token":
            full_text += event["content"]
        elif event["type"] == "meta":
            if "mode" in event:
                mode = event["mode"]
            if "cards" in event:
                cards = event["cards"]
            if "locs" in event:
                locs = event["locs"]
            if "pending" in event:
                pend = event["pending"]
            # Intent is not returned in tuple

    return full_text, mode, pend, locs, cards, det


@app.get("/debug/db")
async def debug_db():
    global db_engine
    if db_engine is None: return {"error": "no db"}
    try:
        count = pd.read_sql("SELECT count(*) FROM reviews", db_engine).iloc[0,0]
        sample = pd.read_sql("SELECT restaurante FROM reviews LIMIT 5", db_engine)
        return {"count": int(count), "sample": sample["restaurante"].tolist()}
    except Exception as e:
        return {"error": str(e)}

# ==========================================
# 4. ENDPOINTS CORE
# ==========================================


@app.get("/")
def read_root():
    return {"status": "online", "message": "API OK v6.8"}


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "version": SERVER_VERSION,
        "lugares": len(df_lugares) if df_lugares is not None else 0,
        "reviews": "LAZY_MODE",
    }


@app.get("/restaurant/{nombre}", response_model=RestaurantDetail)
async def get_restaurant_detail(
    nombre: str, topic: Optional[str] = None, tone: Optional[str] = None
):
    global df_lugares, llm_mini
    t_start = time.time()
    if not nombre:
        raise HTTPException(status_code=404)

    # === CHECK FULL-RESPONSE CACHE ===
    tone = sanitize_tone(tone)
    full_cache_key = f"detail_full_{nombre}_{topic}_{tone}"
    cached_full = await cache.get_json("detail_full", full_cache_key)
    if cached_full:
        print(f"[DETAIL] {nombre} | CACHE HIT | {time.time() - t_start:.2f}s", flush=True)
        return RestaurantDetail(**cached_full)

    # Buscar en df_lugares
    if df_lugares is None or nombre.lower() not in [n.lower() for n in df_lugares.index.tolist()]:
        raise HTTPException(status_code=404, detail="No encontrado")

    # Encontrar el nombre exacto
    nombre_exacto = None
    for n in df_lugares.index.tolist():
        if n.lower() == nombre.lower():
            nombre_exacto = n
            break
    if not nombre_exacto:
        raise HTTPException(status_code=404, detail="No encontrado")

    row = df_lugares.loc[nombre_exacto]
    if isinstance(row, pd.DataFrame): row = row.iloc[0]
    nombre_real = safe_str(row.get("restaurante", nombre_exacto))
    print(f"[DETAIL] {nombre_real} | Metadata lookup: {time.time() - t_start:.2f}s", flush=True)

    # Lazy-load reviews para este restaurante
    t1 = time.time()
    reviews_df = await obtener_reviews_por_local([nombre_exacto])
    reviews_list = []
    if not reviews_df.empty:
        sorted_reviews = rankear_reviews_por_topico(reviews_df, topic)
        for _, r in sorted_reviews.head(8).iterrows():
            if len(safe_str(r.get("texto"))) > 10:
                reviews_list.append(
                    ReviewDetail(
                        autor=formatear_autor(r.get("autor")),
                        rating=safe_int(r.get("rating_user")),
                        texto=safe_str(r.get("texto"))[:2000],
                        fecha=safe_str(r.get("fecha")),
                    )
                )
    print(f"[DETAIL] {nombre_real} | Reviews fetch+rank: {time.time() - t1:.2f}s ({len(reviews_list)} reviews)", flush=True)

    cache_key = f"{nombre_real}_{topic}_{tone}" if topic and topic not in ["undefined", "null"] else f"{nombre_real}__{tone}"
    analisis = await cache.get_json("detail_topic", cache_key)
    if analisis:
        print(f"[DETAIL] {nombre_real} | Analysis CACHE HIT", flush=True)
    else:
        if not topic or topic in ["undefined", "null"]:
            # Bypass LLM: Return pre-calculated summary from df_lugares if no specific topic was requested
            analisis = {
                "resumen": safe_str(row.get("resumen_reviews", "Restaurante recomendado en Neuquén.")),
                "positivos": [],
                "negativos": [],
            }
        else:
            t2 = time.time()
            sample = " | ".join([r.texto[:150] for r in reviews_list[:5]])
            contexto_tema = (
                f"IMPORTANTE: El usuario busca '{topic}'. Resalta qué dicen las reseñas sobre eso."
            )
            prefix = tone_system_instruction(tone)
            prompt_txt = f"""{prefix}\nAnaliza "{nombre_real}". {contexto_tema}
            Responde SOLO JSON válido:
            {{"resumen": "descripción de 2 oraciones...", "positivos": ["p1", "p2"], "negativos": ["n1"]}}
            Reviews: {sample}"""
            try:
                res = await llm_mini.ainvoke(prompt_txt)
                clean = res.content.strip().replace("```json", "").replace("```", "")
                analisis = json.loads(clean)
                asyncio.create_task(cache.set_json("detail_topic", cache_key, analisis))
            except:
                analisis = {
                    "resumen": "Info no disponible momentáneamente.",
                    "positivos": [],
                    "negativos": [],
                }
            print(f"[DETAIL] {nombre_real} | LLM analysis: {time.time() - t2:.2f}s", flush=True)

    result = RestaurantDetail(
        nombre=nombre_real,
        rating=safe_float(row.get("rating_gral")),
        total_reviews=safe_int(row.get("total_reviews_google")),
        direccion=safe_str(row.get("direccion")),
        barrio=safe_str(row.get("barrio")),
        zona=safe_str(row.get("zona")),
        lat=safe_float(row.get("latitud")),
        lng=safe_float(row.get("longitud")),
        resumen_general=safe_str(analisis.get("resumen")),
        aspectos_positivos=analisis.get("positivos", []),
        aspectos_negativos=analisis.get("negativos", []),
        reviews=reviews_list,
    )

    # Cache full response (TTL managed by Redis/Upstash)
    try:
        asyncio.create_task(cache.set_json("detail_full", full_cache_key, result.model_dump()))
    except:
        pass  # Don't fail the request if caching fails

    print(f"[DETAIL] {nombre_real} | TOTAL: {time.time() - t_start:.2f}s", flush=True)
    return result


@app.post("/chat/stream")
async def chat_stream(req: QueryRequest, request: Request):
    start_time = asyncio.get_event_loop().time()

    # 1. Setup Context
    ctx = req.conversation_context.copy() if req.conversation_context else {}
    if req.tone:
        ctx["tone"] = sanitize_tone(req.tone)
    client_ip = extract_client_ip(request)

    async def event_generator():
        t_gen_start = time.time()
        first_token_sent = False

        # === FLUSH PROXY BUFFER ===
        # Fly.io's reverse proxy buffers small response chunks.
        # Send a 4KB padding to exceed the proxy's buffer threshold,
        # forcing it to forward all subsequent chunks immediately.
        yield ": " + " " * 4096 + "\n\n"
        print(f"[STREAM] {time.time() - t_gen_start:.2f}s | Flush padding sent (4KB)", flush=True)

        # Accumulators for Logging and Context
        full_text = ""
        mode = "general"
        cards = []
        locs = []
        pend = None
        zona = None
        intent_debug = ""

        # LOG DE ENTRADA — visible en Fly.io
        logger.info(
            f"📥 QUERY | '{req.query}' | ip={client_ip} | tone={req.tone or '-'}"
        )

        try:
            async for event in procesar_consulta_gen(
                req.query, df_lugares, vectorstore, llm_mini, llm_smart, ctx, user_ip=client_ip
            ):
                # Update accumulators
                if event["type"] == "token":
                    full_text += event["content"]
                    if not first_token_sent:
                        first_token_sent = True
                        print(f"[STREAM] {time.time() - t_gen_start:.2f}s | First token YIELDED to client", flush=True)
                elif event["type"] == "debug":
                    # Capturar intent del debug message para el log final
                    msg = event.get("message", "")
                    if "🧠 INTENCIóN:" in msg or "INTENCION" in msg.upper():
                        intent_debug = msg
                elif event["type"] == "meta":
                    if "mode" in event:
                        mode = event["mode"]
                    if "cards" in event:
                        cards = event["cards"]
                        # Convert Pydantic models to dicts for JSON serialization
                        event["cards"] = [
                            c.model_dump() if hasattr(c, "model_dump") else c.dict()
                            for c in cards
                        ]
                    if "locs" in event:
                        locs = event["locs"]
                    if "pending" in event:
                        pend = event["pending"]
                    if "zona" in event:
                        zona = event["zona"]

                # Encode and yield as SSE data event
                yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
        except Exception as e:
            logger.error(f"❌ Stream error | query='{req.query}' | error={e}")
            yield "data: " + json.dumps(
                {"type": "error", "message": str(e)}, ensure_ascii=False
            ) + "\n\n"

        # === POST-STREAMING LOGIC ===

        # 1. Update Context (Same logic as sync chat)
        new_ctx = ctx.copy()
        if pend:
            new_ctx["pending_options"] = pend
        elif "pending_options" in new_ctx:
            del new_ctx["pending_options"]

        if (
            req.conversation_context
            and "original_query" in req.conversation_context
            and "original_query" not in new_ctx
        ):
            new_ctx["original_query"] = req.conversation_context["original_query"]

        # Yield final context update event
        yield "data: " + json.dumps(
            {"type": "context_update", "context": new_ctx}, ensure_ascii=False
        ) + "\n\n"

        # 2. Logging
        response_time = asyncio.get_event_loop().time() - start_time
        restaurants = [c.nombre for c in cards] if cards else []
        rest_str = (
            ", ".join(restaurants[:4]) + (" ..." if len(restaurants) > 4 else "")
            if restaurants
            else "-"
        )
        resp_preview = full_text[:120].replace("\n", " ") if full_text else "-"

        logger.info(
            f"📤 RESP | mode={mode} | zona={zona or 'global'} | "
            f"lugares={len(restaurants)} ({rest_str}) | "
            f"{response_time:.1f}s | preview='{resp_preview}'"
        )

        ai_provider = None
        if llm_mini:
            ai_provider = f"Mini:{llm_mini.model_name}"
        if llm_smart and mode in ["rag", "resumen"]:
            ai_provider = f"Smart:{llm_smart.model_name}"
        asyncio.create_task(
            log_user_query_to_discord(
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
                strikes=ctx.get("strikes", 0),
                zona_detectada=zona,
            )
        )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
        },
    )


@app.post("/chat", response_model=QueryResponse)
async def chat(req: QueryRequest, request: Request):
    start_time = asyncio.get_event_loop().time()

    try:
        # LOG DE ENTRADA: ¿Qué contexto me manda el frontend?
        logger.info(f"📥 Contexto Recibido: {req.conversation_context}")
        ctx = req.conversation_context.copy() if req.conversation_context else {}
        if req.tone:
            ctx["tone"] = sanitize_tone(req.tone)

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
        asyncio.create_task(
            log_user_query_to_discord(
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
                strikes=ctx.get("strikes", 0),
            )
        )

        new_ctx = ctx.copy() if ctx else {}
        if pend:
            new_ctx["pending_options"] = pend
        elif "pending_options" in new_ctx:
            del new_ctx["pending_options"]

        # if req.conversation_context and 'last_entity' in req.conversation_context and 'last_entity' not in new_ctx:
        #      new_ctx['last_entity'] = req.conversation_context['last_entity']
        if (
            req.conversation_context
            and "original_query" in req.conversation_context
            and "original_query" not in new_ctx
        ):
            new_ctx["original_query"] = req.conversation_context["original_query"]

        # LOG DE SALIDA: ¿Qué contexto le devuelvo?
        logger.info(f"📤 Contexto Saliente: {new_ctx}")

        return QueryResponse(
            response=resp,
            mode=mode,
            conversation_context=new_ctx,
            locations=locs,
            restaurant_cards=cards,
            detail_content=det,
        )
    except Exception as e:
        logger.error(f"Error Chat: {e}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
