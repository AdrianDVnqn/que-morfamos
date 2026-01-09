"""
Script para normalizar los CSVs antes de migrar a PostgreSQL.
Separa datos de lugares y reviews que están mezclados en reviews_neuquen.csv
"""
import pandas as pd

def normalize_data():
    print("📊 Cargando reviews_neuquen.csv...")
    df = pd.read_csv("data/reviews_neuquen.csv")
    print(f"   Total filas: {len(df)}")
    print(f"   Columnas: {list(df.columns)}")
    
    # Columnas que pertenecen a LUGARES (datos del restaurante)
    lugar_columns = [
        'restaurante',  # será la PK
        'categoria', 
        'rating_gral', 
        'total_reviews_google', 
        'direccion', 
        'latitud', 
        'longitud', 
        'url',
        'barrio', 
        'zona', 
        'cerca_rio'
    ]
    
    # Columnas que pertenecen a REVIEWS (datos de la reseña)
    review_columns = [
        'restaurante',  # FK a lugares
        'review_id',    # PK
        'autor', 
        'rating_user', 
        'texto', 
        'fecha_aproximada', 
        'fecha_original',
        'fecha_scraping'
    ]
    
    # 1. Extraer lugares únicos
    print("\n🏪 Extrayendo lugares únicos...")
    df_lugares = df[lugar_columns].drop_duplicates(subset=['restaurante'])
    df_lugares = df_lugares.rename(columns={'restaurante': 'nombre'})
    print(f"   Lugares únicos: {len(df_lugares)}")
    
    # 2. Crear tabla de reviews limpia
    print("\n📝 Creando tabla de reviews limpia...")
    df_reviews = df[review_columns].copy()
    print(f"   Reviews totales: {len(df_reviews)}")
    
    # 3. Guardar CSVs normalizados
    output_lugares = "data/lugares_normalized.csv"
    output_reviews = "data/reviews_normalized.csv"
    
    df_lugares.to_csv(output_lugares, index=False)
    print(f"\n✅ Guardado: {output_lugares}")
    
    df_reviews.to_csv(output_reviews, index=False)
    print(f"✅ Guardado: {output_reviews}")
    
    # 4. Mostrar resumen
    print("\n" + "=" * 50)
    print("📋 RESUMEN DE NORMALIZACIÓN")
    print("=" * 50)
    print(f"\n📁 {output_lugares}")
    print(f"   Columnas: {list(df_lugares.columns)}")
    print(f"   Filas: {len(df_lugares)}")
    
    print(f"\n📁 {output_reviews}")
    print(f"   Columnas: {list(df_reviews.columns)}")
    print(f"   Filas: {len(df_reviews)}")
    
    return df_lugares, df_reviews

if __name__ == "__main__":
    normalize_data()
    print("\n🎉 Normalización completada!")
    print("   Ahora podés ejecutar: python migrate_data.py")
