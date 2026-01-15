import os
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine

load_dotenv("mis_claves.env")
DATABASE_URL = os.getenv("DATABASE_URL")

def check_places():
    engine = create_engine(DATABASE_URL)
    query = """
    SELECT nombre as restaurante, zona, barrio, direccion 
    FROM lugares 
    WHERE nombre ILIKE '%%cerveceria owe%%' 
       OR nombre ILIKE '%%bamb%%' 
       OR nombre ILIKE '%%ribera%%'
       OR nombre ILIKE '%%bordelesa%%'
       OR nombre ILIKE '%%carritos%%'
       OR nombre ILIKE '%%patagonia%%refugio%%'
    """
    try:
        df = pd.read_sql(query, engine)
        pd.set_option('display.max_colwidth', 50)
        pd.set_option('display.width', None)
        print(df.to_string())
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_places()
