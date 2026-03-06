import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv('mis_claves.env')

DATABASE_URL = os.getenv("DATABASE_URL")

def check_places():
    engine = create_engine(DATABASE_URL)
    
    query = text("""
    SELECT nombre, rating_gral, total_reviews_google, zona, barrio, categoria
    FROM lugares
    WHERE nombre IN ('Crafter', 'Bunker Bar Patio Cervecero', 'Los Carritos', 'Cerveceria Owe', 'Cerveza Patagonia - Refugio Neuquén')
    """)
    
    with engine.connect() as conn:
        result = conn.execute(query)
        rows = result.fetchall()
        for row in rows:
            print(row)

if __name__ == "__main__":
    check_places()
