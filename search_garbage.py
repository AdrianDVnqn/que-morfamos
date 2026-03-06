import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv('mis_claves.env')
DATABASE_URL = os.getenv("DATABASE_URL")

def search_pasteleria():
    if not DATABASE_URL:
        print("No DATABASE_URL found")
        return

    engine = create_engine(DATABASE_URL)
    tables = ["lugares", "reviews", "reviews_embeddings"] # Main tables
    
    with engine.connect() as conn:
        for table in tables:
            # Check if table exists first (optional but safer)
            query = text(f"SELECT count(*) FROM {table} WHERE restaurante ILIKE 'PASTELERIA' OR nombre ILIKE 'PASTELERIA'")
            try:
                # Need to handle different column names (restaurante vs nombre)
                if table == "lugares":
                    query = text(f"SELECT count(*) FROM {table} WHERE nombre = 'PASTELERIA'")
                else:
                    query = text(f"SELECT count(*) FROM {table} WHERE restaurante = 'PASTELERIA'")
                
                count = conn.execute(query).scalar()
                print(f"Table '{table}': {count} occurrences")
            except Exception as e:
                print(f"Table '{table}' error or column not found: {e}")

if __name__ == "__main__":
    search_pasteleria()
