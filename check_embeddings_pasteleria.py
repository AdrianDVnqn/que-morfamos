import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv('mis_claves.env')
DATABASE_URL = os.getenv("DATABASE_URL")

def check_embeddings():
    engine = create_engine(DATABASE_URL)
    query = text("""
        SELECT count(*) 
        FROM langchain_pg_embedding 
        WHERE cmetadata->>'nombre' = 'PASTELERIA' 
           OR cmetadata->>'restaurante' = 'PASTELERIA'
    """)
    with engine.connect() as conn:
        count = conn.execute(query).scalar()
        print(f"Embeddings for 'PASTELERIA': {count}")

if __name__ == "__main__":
    check_embeddings()
