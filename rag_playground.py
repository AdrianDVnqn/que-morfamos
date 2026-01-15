import os
import pandas as pd
import numpy as np
import asyncio
import math
from dotenv import load_dotenv
from sqlalchemy import create_engine
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_community.vectorstores import FAISS
import unicodedata
import re

# Colorines para la consola
from colorama import Fore, Style, init
init()

# Cargar variables
load_dotenv("mis_claves.env")

# Configurar LLM (Mini para pruebas rápidas)
openai_key = os.getenv("OPENAI_API_KEY")
llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, api_key=openai_key)
embeddings = OpenAIEmbeddings(model="text-embedding-3-small", api_key=openai_key)

# Variables Globales
df = None
vectorstore = None

def load_data():
    """Carga los datos igual que main.py pero sin FastAPI"""
    global df
    print(f"{Fore.CYAN}⏳ Cargando datos desde DB...{Style.RESET_ALL}")
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print(f"{Fore.RED}❌ Falta DATABASE_URL en .env{Style.RESET_ALL}")
        return

    try:
        engine = create_engine(db_url)
        query = """
            SELECT 
                r.restaurante, r.autor, r.rating_user, r.texto, 
                r.fecha_aproximada as fecha, r.review_id,
                l.rating_gral, l.total_reviews_google, l.direccion,
                l.barrio, l.zona, l.categoria
            FROM reviews r
            LEFT JOIN lugares l ON r.restaurante = l.nombre
        """
        df = pd.read_sql(query, engine)
        
        # Limpieza básica
        cols = ['restaurante', 'texto', 'direccion', 'barrio', 'zona', 'categoria']
        for col in cols:
            if col in df.columns:
                df[col] = df[col].fillna("").astype(str).str.strip()
        
        # Filtro Neuquén
        mask_neuquen = df['direccion'].str.contains('Q8300|Q8301|Q8302', case=False, na=False)
        df = df[mask_neuquen]
        
        # Rating numérico
        df['rating_gral'] = df['rating_gral'].astype(str).str.replace(',', '.', regex=False)
        df['rating_gral'] = pd.to_numeric(df['rating_gral'], errors='coerce').fillna(0.0)
        
        print(f"{Fore.GREEN}✅ Datos cargados: {len(df)} reviews.{Style.RESET_ALL}")
    except Exception as e:
        print(f"{Fore.RED}❌ Error cargando datos: {e}{Style.RESET_ALL}")

def load_vectors():
    """Carga el índice vectorial local"""
    global vectorstore
    print(f"{Fore.CYAN}⏳ Cargando VectorStore FAISS...{Style.RESET_ALL}")
    try:
        if os.path.exists("faiss_index_react"):
            vectorstore = FAISS.load_local("faiss_index_react", embeddings, allow_dangerous_deserialization=True)
            print(f"{Fore.GREEN}✅ VectorStore cargado.{Style.RESET_ALL}")
        else:
            print(f"{Fore.RED}⚠️ No existe 'faiss_index_react'. No se podrá probar búsqueda vectorial.{Style.RESET_ALL}")
    except Exception as e:
        print(f"{Fore.RED}❌ Error cargando Faiss: {e}{Style.RESET_ALL}")

# ==========================================
# 🧪 TUS EXPERIMENTOS AQUÍ
# ==========================================

def experiment_hybrid_retrieval(query, keyword_to_force=None):
    """
    Prueba el enfoque Híbrido: Vectorial + Keyword Frecuency.
    Si 'keyword_to_force' se da, buscamos qué lugares la mencionan mucho.
    """
    print(f"\n🧪 {Fore.YELLOW}EXPERIMENTO: Query='{query}' | ForceKeyword='{keyword_to_force}'{Style.RESET_ALL}")
    
    candidates = {} # {nombre: {'source': 'vector'/'keyword', 'score': ...}}
    
    # 1. Búsqueda Vectorial (Lo que ya tenemos)
    if vectorstore:
        print(f"   🔎 Buscando vectores...")
        docs = vectorstore.similarity_search(query, k=20) # Top 20 vectores
        for i, d in enumerate(docs):
            name = d.metadata.get('nombre')
            if name not in candidates:
                candidates[name] = {'source': 'vector', 'rank_vec': i+1, 'mentions': 0}
    
    # 2. Búsqueda por Keyword en Reviews (Lo NUEVO)
    if keyword_to_force:
        print(f"   🔎 Buscando keyword '{keyword_to_force}' en reviews...")
        # Filtrar reviews que contengan la palabra
        mask = df['texto'].str.contains(keyword_to_force, case=False, na=False)
        matches = df[mask]
        
        # Contar menciones por restaurante
        counts = matches['restaurante'].value_counts()
        
        # Tomar el Top 10 de lugares con más menciones
        top_keyword_places = counts.head(10)
        
        print(f"      📊 Lugares con más menciones de '{keyword_to_force}':")
        for place, count in top_keyword_places.items():
            print(f"         - {place}: {count} menciones")
            
            if place not in candidates:
                # INYECCIÓN: Si no estaba por vector, lo metemos!
                candidates[place] = {'source': 'keyword_injection', 'rank_vec': 999, 'mentions': count}
            else:
                # Si ya estaba, anotamos que tiene menciones fuertes
                candidates[place]['source'] = 'hybrid' # Vector + Keyword
                candidates[place]['mentions'] = count

    # 3. Mostrar la lista fusionada
    print(f"\n   📋 {Fore.WHITE}Lista Fusionada de Candidatos (Pre-Juez):{Style.RESET_ALL}")
    sorted_cands = sorted(candidates.items(), key=lambda x: (x[1].get('mentions', 0), -x[1].get('rank_vec', 999)), reverse=True)
    
    for name, meta in sorted_cands[:15]: # Top 15 final
        src_color = Fore.GREEN if meta['source'] == 'hybrid' else (Fore.BLUE if meta['source'] == 'vector' else Fore.MAGENTA)
        print(f"      {src_color}[{meta['source'].upper()}]{Style.RESET_ALL} {name} (VecRank: {meta.get('rank_vec')}, Menciones: {meta.get('mentions')})")
        
    return list(candidates.keys())

async def run_playground():
    # Carga inicial única
    load_data()
    load_vectors()
    
    if df is None: return

    while True:
        print("\n" + "="*50)
        print("🤖 RAG PLAYGROUND")
        print("Escribe una query para probar (o 'exit').")
        print("Formato: 'query' O 'query | keyword_extra'")
        print("Ejemplo: 'donde comer milanesa | milanesa'")
        
        user_input = input(f"{Fore.GREEN}>> {Style.RESET_ALL}")
        if user_input.lower() in ['exit', 'quit']: break
        
        parts = user_input.split('|')
        query = parts[0].strip()
        kw = parts[1].strip() if len(parts) > 1 else None
        
        # Ejecutar experimeto
        experiment_hybrid_retrieval(query, kw)

if __name__ == "__main__":
    asyncio.run(run_playground())
