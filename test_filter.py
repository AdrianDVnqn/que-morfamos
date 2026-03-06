import os
import pandas as pd
from sqlalchemy import create_engine
from dotenv import load_dotenv

load_dotenv('mis_claves.env')

DATABASE_URL = os.getenv("DATABASE_URL")

def check_filtering():
    engine = create_engine(DATABASE_URL)
    
    query = "SELECT nombre, direccion FROM lugares"
    df = pd.read_sql(query, engine)
    
    total = len(df)
    mask = df['direccion'].str.contains('Q8300|Q8301|Q8302', case=False, na=False)
    filtered = df[mask]
    
    not_filtered = df[~mask]
    
    print(f"Total: {total}")
    print(f"Pasan filtro Q830x: {len(filtered)}")
    print(f"Se quedan afuera: {len(not_filtered)}")
    
    print("\nEjemplos de los que se quedan afuera:")
    print(not_filtered.head(20).to_string())
    
    print("\n¿Está Crafter?")
    print(not_filtered[not_filtered['nombre'] == 'Crafter'])

if __name__ == "__main__":
    check_filtering()
