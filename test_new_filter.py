import os
import pandas as pd
from sqlalchemy import create_engine
from dotenv import load_dotenv

load_dotenv('mis_claves.env')

DATABASE_URL = os.getenv("DATABASE_URL")

def test_new_filter():
    engine = create_engine(DATABASE_URL)
    query = "SELECT nombre, direccion FROM lugares"
    df = pd.read_sql(query, engine)
    
    mask_cp = df['direccion'].str.contains('Q8300|Q8301|Q8302', case=False, na=False)
    mask_city = df['direccion'].str.contains('Neuquén', case=False, na=False) & \
                ~df['direccion'].str.contains('Cipolletti|Plottier|Centenario|Senillosa|Añelo|R8324', case=False, na=False)
    
    mask_final = mask_cp | mask_city
    
    print(f"Total: {len(df)}")
    print(f"Pasan con filtro viejo: {mask_cp.sum()}")
    print(f"Pasan con filtro nuevo: {mask_final.sum()}")
    print(f"Diferencia (nuevos): {mask_final.sum() - mask_cp.sum()}")
    
    print("\nEjemplos de recuperados:")
    recuperados = df[mask_final & ~mask_cp]
    print(recuperados.head(20).to_string())
    
    if 'Crafter' in recuperados['nombre'].values:
        print("\n✅ Crafter RECUPERADO")
    else:
        print("\n❌ Crafter sigue afuera")

if __name__ == "__main__":
    test_new_filter()
