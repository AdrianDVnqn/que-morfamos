from main import df
import json

def inspect():
    print("📊 Columnas dispobibles:", df.columns.tolist())
    
    # Identificar nombres de columnas clave
    col_cat = 'rubro' if 'rubro' in df.columns else ('categoria' if 'categoria' in df.columns else None)
    
    report = {}
    
    if col_cat:
        cats = sorted(df[col_cat].dropna().astype(str).unique().tolist())
        report['categorias'] = cats
        print(f"✅ {len(cats)} Categorías encontradas")
    else:
        print("❌ No encontré columna de categoría/rubro")
    
    if 'zona' in df.columns:
        zonas = sorted(df['zona'].dropna().astype(str).unique().tolist())
        report['zonas'] = zonas
        print(f"✅ {len(zonas)} Zonas encontradas")

    if 'barrio' in df.columns:
        barrios = sorted(df['barrio'].dropna().astype(str).unique().tolist())
        report['barrios'] = barrios
        print(f"✅ {len(barrios)} Barrios encontrados")

    with open("unique_values.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
        
    print("💾 Guardado en unique_values.json")

if __name__ == "__main__":
    inspect()
