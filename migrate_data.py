"""
Migración completa de datos a PostgreSQL con pgvector.
1. Sube reviews_neuquen.csv a tabla 'reviews'
2. Sube lugares_validados.csv a tabla 'lugares'  
3. Genera embeddings para búsqueda vectorial

Ejecutar una sola vez: python migrate_data.py
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

# Convertir URL para compatibilidad con SQLAlchemy + psycopg
def get_sqlalchemy_url(url):
    """Convierte postgres:// a postgresql+psycopg:// para SQLAlchemy"""
    if url and url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    elif url and url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url

SQLALCHEMY_URL = get_sqlalchemy_url(DATABASE_URL)


def migrate_tables(engine):
    """Sube CSVs normalizados a tablas PostgreSQL"""
    
    # Lugares (datos del restaurante)
    print("🏪 Cargando lugares_normalized.csv...")
    df_lugares = pd.read_csv("data/lugares_normalized.csv")
    print(f"   Columnas: {list(df_lugares.columns)}")
    df_lugares.to_sql("lugares", engine, if_exists="replace", index=False)
    print(f"✅ {len(df_lugares)} lugares migrados a tabla 'lugares'")
    
    # Reviews (reseñas)
    print("\n📝 Cargando reviews_normalized.csv...")
    df_reviews = pd.read_csv("data/reviews_normalized.csv")
    print(f"   Columnas: {list(df_reviews.columns)}")
    df_reviews.to_sql("reviews", engine, if_exists="replace", index=False)
    print(f"✅ {len(df_reviews)} reviews migradas a tabla 'reviews'")
    
    return df_lugares, df_reviews

def generate_embeddings(df_lugares, df_reviews):
    """Genera embeddings para búsqueda vectorial con pgvector"""
    
    print("\n🔍 Generando embeddings para búsqueda semántica...")
    
    # Crear documentos usando metadata de lugares y contenido de reviews
    docs = []
    
    for i, (_, lugar) in enumerate(df_lugares.iterrows()):
        nombre = lugar['nombre']
        
        # Buscar reviews de este restaurante
        rest_reviews = df_reviews[df_reviews['restaurante'] == nombre]
        
        # Combinamos las mejores reviews como contexto para embeddings
        # 20 reviews x 500 chars = ~10,000 chars (bien dentro del límite del modelo)
        textos = rest_reviews['texto'].fillna("").head(20).tolist()
        contenido = " | ".join([str(t)[:500] for t in textos if len(str(t)) > 10])
        
        if contenido:
            # Extraer rating de forma segura (puede venir como "4,5" en español)
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
        
        # Progress indicator
        if (i + 1) % 50 == 0:
            print(f"   Procesados {i + 1}/{len(df_lugares)} lugares...")
    
    print(f"📝 {len(docs)} lugares listos para embeddings")
    
    # Crear vectorstore con PGVector
    print("🚀 Subiendo embeddings a PostgreSQL (esto puede tardar unos minutos)...")
    embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
    
    vectorstore = PGVector.from_documents(
        documents=docs,
        embedding=embeddings,
        connection=DATABASE_URL,
        collection_name=COLLECTION_NAME,
        use_jsonb=True
    )
    
    print("✅ Embeddings generados y guardados en PostgreSQL!")
    return len(docs)

def main():
    if not DATABASE_URL:
        print("❌ ERROR: DATABASE_URL no configurada en mis_claves.env")
        print("   Agregá: DATABASE_URL=postgres://postgres:PASSWORD@host:5432/database")
        return
    
    print("🚀 Iniciando migración a PostgreSQL...")
    print(f"   Database: {DATABASE_URL.split('@')[1] if '@' in DATABASE_URL else 'configurada'}")
    
    engine = create_engine(SQLALCHEMY_URL)
    
    # Habilitar extensión pgvector
    print("\n🔧 Habilitando extensión pgvector...")
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.commit()
    print("✅ Extensión pgvector habilitada")
    
    # Migrar tablas de datos
    df_lugares, df_reviews = migrate_tables(engine)
    
    # Generar embeddings
    num_embeddings = generate_embeddings(df_lugares, df_reviews)
    
    print("\n" + "=" * 50)
    print("🎉 MIGRACIÓN COMPLETADA!")
    print("=" * 50)
    print(f"   - Lugares migrados: {len(df_lugares)}")
    print(f"   - Reviews migradas: {len(df_reviews)}")
    print(f"   - Embeddings generados: {num_embeddings}")
    print("   - Tablas creadas: reviews, lugares, langchain_pg_embedding")

if __name__ == "__main__":
    main()
