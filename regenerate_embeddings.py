"""
Regenera solo los embeddings (sin recargar las tablas).
Útil cuando cambiás parámetros como cantidad de reviews o largo de texto.
"""
import os
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from langchain_openai import OpenAIEmbeddings
from langchain_postgres import PGVector
from langchain_core.documents import Document

load_dotenv("mis_claves.env")

DATABASE_URL = os.getenv("DATABASE_URL")
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "reviews_embeddings")

def get_sqlalchemy_url(url):
    if url and url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg2://", 1)
    elif url and url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url

SQLALCHEMY_URL = get_sqlalchemy_url(DATABASE_URL)

def regenerate_embeddings():
    print("🔍 Regenerando embeddings con más contexto...")
    
    engine = create_engine(SQLALCHEMY_URL)
    
    # Cargar datos desde PostgreSQL
    print("📊 Cargando datos desde PostgreSQL...")
    df_lugares = pd.read_sql("SELECT * FROM lugares", engine)
    df_reviews = pd.read_sql("SELECT * FROM reviews", engine)
    print(f"   - {len(df_lugares)} lugares")
    print(f"   - {len(df_reviews)} reviews")
    
    # Limpiar embeddings anteriores
    print("\n🗑️ Limpiando embeddings anteriores...")
    with engine.connect() as conn:
        conn.execute(text(f"DELETE FROM langchain_pg_embedding WHERE collection_id IN (SELECT uuid FROM langchain_pg_collection WHERE name = '{COLLECTION_NAME}')"))
        conn.commit()
    
    # Generar nuevos embeddings
    print("\n🔍 Generando embeddings (20 reviews x 300 chars por lugar)...")
    docs = []
    
    for i, (_, lugar) in enumerate(df_lugares.iterrows()):
        nombre = lugar['nombre']
        rest_reviews = df_reviews[df_reviews['restaurante'] == nombre]
        
        # 20 reviews x 500 chars = ~10,000 chars (bien dentro del límite)
        textos = rest_reviews['texto'].fillna("").head(20).tolist()
        contenido = " | ".join([str(t)[:500] for t in textos if len(str(t)) > 10])
        
        if contenido:
            rating_raw = lugar.get('rating_gral', 0)
            try:
                if isinstance(rating_raw, str):
                    rating_raw = rating_raw.replace(',', '.')
                rating = float(rating_raw) if rating_raw else 0.0
            except:
                rating = 0.0
            
            doc = Document(
                page_content=contenido,
                metadata={
                    "nombre": str(nombre),
                    "rating": rating,
                    "direccion": str(lugar.get('direccion', '') or ''),
                    "zona": str(lugar.get('zona', '') or ''),
                    "barrio": str(lugar.get('barrio', '') or ''),
                    "categoria": str(lugar.get('categoria', '') or '')
                }
            )
            docs.append(doc)
        
        if (i + 1) % 100 == 0:
            print(f"   Procesados {i + 1}/{len(df_lugares)} lugares...")
    
    print(f"📝 {len(docs)} lugares listos para embeddings")
    
    print("🚀 Subiendo embeddings a PostgreSQL...")
    embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
    
    vectorstore = PGVector.from_documents(
        documents=docs,
        embedding=embeddings,
        connection=DATABASE_URL,
        collection_name=COLLECTION_NAME,
        use_jsonb=True
    )
    
    print(f"\n✅ {len(docs)} embeddings regenerados!")

if __name__ == "__main__":
    regenerate_embeddings()
