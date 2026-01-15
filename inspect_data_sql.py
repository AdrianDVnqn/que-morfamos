import os
import json
import pandas as pd
from sqlalchemy import create_engine
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv("mis_claves.env")

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    # Intento fallback manual si no está en env (esto es solo para dev local)
    # Por seguridad no imprimo la URL
    print("❌ No se encontró DATABASE_URL en variables de entorno.")
    exit(1)

def inspect_sql():
    print("🔌 Conectando a Supabase via SQL...")
    try:
        engine = create_engine(DATABASE_URL)
        
        # Consultar valores únicos de la tabla LUGARES directamente
        query = """
            SELECT DISTINCT categoria, barrio, zona 
            FROM lugares
            ORDER BY categoria, barrio, zona
        """
        
        print("📊 Ejecutando Query...")
        df = pd.read_sql(query, engine)
        
        report = {}
        
        # Categorías
        cats = sorted(df['categoria'].dropna().astype(str).unique().tolist())
        report['categorias'] = cats
        print(f"✅ {len(cats)} Categorías encontradas")

        # Zonas
        zonas = sorted(df['zona'].dropna().astype(str).unique().tolist())
        report['zonas'] = zonas
        print(f"✅ {len(zonas)} Zonas encontradas")

        # Barrios
        barrios = sorted(df['barrio'].dropna().astype(str).unique().tolist())
        report['barrios'] = barrios
        print(f"✅ {len(barrios)} Barrios encontrados")

        with open("unique_values.json", "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
            
        print("💾 Guardado en unique_values.json")
        
    except Exception as e:
        print(f"❌ Error SQL: {e}")

if __name__ == "__main__":
    inspect_sql()
