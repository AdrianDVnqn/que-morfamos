import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv('mis_claves.env')

DATABASE_URL = os.getenv("DATABASE_URL")

def check_crafter():
    if not DATABASE_URL:
        print("No DATABASE_URL found")
        return

    engine = create_engine(DATABASE_URL)
    
    query = text("""
    SELECT nombre, rating_gral, total_reviews_google, direccion, barrio, zona, categoria
    FROM lugares
    WHERE nombre ILIKE '%crafter%'
    """)
    
    with engine.connect() as conn:
        result = conn.execute(query)
        rows = result.fetchall()
        
        if not rows:
            print("Crafter no encontrado.")
        else:
            for row in rows:
                print(row)

if __name__ == "__main__":
    check_crafter()
