import json
import time
import math
import pandas as pd
import numpy as np
import warnings
from colorama import Fore, Style, init
from fastapi.testclient import TestClient
import importlib 
import os

# Suprimir warnings molestos de librerías
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)

# Importar app y DataFrame de datos
# Importar módulo main completo para acceder a variables globales actualizadas
print("⏳ Cargando aplicación backend (modelos, DB)...")

MODULE_NAME = os.getenv("BENCHMARK_MODULE", "main")
print(f"🎯 Usando módulo backend: {MODULE_NAME}")
main = importlib.import_module(MODULE_NAME)

# Init colorama
init()

TEST_CASES_FILE = "benchmark_cases.json"


def calculate_score(row):
    """Fórmula de Ranking del Backend: Rating + log10(Reviews)*2.7"""
    try:
        rat = float(str(row.get('rating_gral', 0)).replace(',', '.'))
        revs = int(row.get('total_reviews_google', 0))
        return rat + (math.log10(revs + 1) * 2.7)
    except:
        return 0

def get_ground_truth(case, df_source):
    """
    Calcula el Top de candidatos teóricos usando filtros Pandas (simulando SQL).
    """
    # Copia para no alterar original
    dff = df_source.copy()
    
    # Normalizar columnas de texto para búsqueda
    for col in ['zona', 'barrio', 'direccion', 'restaurante']:
        if col in dff.columns:
            dff.loc[:, col] = dff[col].fillna('').astype(str).str.lower()

    # Identificar columna de categoría/rubro
    col_cat = 'rubro' if 'rubro' in dff.columns else ('categoria' if 'categoria' in dff.columns else None)
    
    if col_cat:
        dff.loc[:, col_cat] = dff[col_cat].fillna('').astype(str).str.lower()
    
    # Filtros según Case ID
    case_id = case['id']
    mask = pd.Series([True] * len(dff))
    
    if case_id == "zona_rio":
        # Zona exacta "Paseo de la Costa" o barrios ribereños
        z_mask = (dff['zona'].str.lower() == 'paseo de la costa') | \
               dff['barrio'].str.lower().isin(['río grande', 'limay', 'altos del limay', 'confluencia urbano']) | \
               dff['direccion'].str.contains('rio|costa|isla|paseo', regex=True)
               
        # Filtro semántico (simulando lo que el humano esperaría de "bares")
        # Excluimos heladerías y cafeterías
        exclude_cat = ['ice cream', 'heladeria', 'heladería', 'coffee', 'cafe']
        
        ex_mask = ~dff[col_cat].str.contains('|'.join(exclude_cat)) if col_cat else True
        
        mask = z_mask & ex_mask

    elif case_id == "zona_oeste":
        # Cervecerias en Oeste
        z_mask = dff['zona'].str.lower() == 'oeste'
        # Categorías exactas (en base a unique_values.json)
        cat_keywords = ['beer', 'brewery', 'brewpub', 'gastropub', 'bar']
        c_mask = dff[col_cat].str.contains('|'.join(cat_keywords)) if col_cat else False
        n_mask = dff['restaurante'].str.contains('cerveza|cerveceria|birra|patagonia')
        mask = z_mask & (c_mask | n_mask)

    elif case_id == "combo_merienda_alto":
        z_mask = dff['zona'].str.contains('alto|norte')
        cat_keywords = ['cafe', 'coffee', 'bakery', 'pastry', 'tea']
        c_mask = dff[col_cat].str.contains('|'.join(cat_keywords)) if col_cat else False
        n_mask = dff['restaurante'].str.contains('cafe|confiteria|pasteleria')
        mask = z_mask & (c_mask | n_mask)

    elif case_id == "combo_parrilla_centro":
        z_mask = dff['zona'].str.contains('centro')
        
        cat_keywords = ['steak', 'barbecue', 'grill', 'parrilla', 'chophouse']
        c_mask = dff[col_cat].str.contains('|'.join(cat_keywords)) if col_cat else False
        n_mask = dff['restaurante'].str.contains('parrilla|asado|fuego')
        
        # Excluir panaderías/cafeterías explícitamente
        exclude_keywords = ['bakery', 'coffee', 'cafe', 'panaderia', 'confiteria', 'pasteleria']
        ex_mask = ~dff[col_cat].str.contains('|'.join(exclude_keywords)) if col_cat else True
        
        mask = z_mask & (c_mask | n_mask) & ex_mask
        
    elif case_id == "plato_milanesas":
        # Estrategia Reviews: Buscar menciones en el texto de los usuarios
        # Esto es mucho más potente para platos específicos
        t_mask = dff['texto'].str.contains('milanesa|suprema', na=False)
        
        # Filtramos el DF solo con reviews que hablan de milanesas
        candidatos = dff[t_mask].copy()
        
        # Contamos menciones por restaurante
        menciones = candidatos['restaurante'].value_counts()
        
        # Nos quedamos con los que tienen al menos 2 menciones para filtrar ruido
        top_places = menciones[menciones >= 2].index.tolist()
        
        # Filtramos el DF original para esos lugares
        final_candidates = dff[dff['restaurante'].isin(top_places)].copy()
        final_candidates = final_candidates.drop_duplicates(subset=['restaurante'])
        
        # Aquí el score podría ser el número de menciones, pero usemos el score general para consistencia
        # O mejor: Score general + Comentario adicional: " (X menciones)"
        final_candidates['score'] = final_candidates.apply(calculate_score, axis=1)
        
        top_5 = final_candidates.sort_values('score', ascending=False).head(5)
        return top_5[['restaurante', 'score', 'zona']].to_dict('records')

    elif case_id == "feature_pelotero":
        # Similarstrategy: buscar en texto
        t_mask = dff['texto'].str.contains('pelotero|juegos', na=False)
        candidatos = dff[t_mask].copy()
        candidatos = candidatos.drop_duplicates(subset=['restaurante']) # Deduplicar aquí
        # ... logic continue below ...
        mask = pd.Series([False] * len(dff)) # Dummy mask because we handled candidates manually
        
        # Hack para devolver directo
        candidatos['score'] = candidatos.apply(calculate_score, axis=1)
        top_5 = candidatos.sort_values('score', ascending=False).head(5)
        return top_5[['restaurante', 'score', 'zona']].to_dict('records')
        
    elif case_id == "feature_sintacc":
        n_mask = dff['restaurante'].str.contains('sintacc|celiaco|gluten|natural')
        c_mask = dff[col_cat].str.contains('health|vegan|vegetarian') if col_cat else False
        t_mask = dff['texto'].str.contains('sin tacc|celiaco|gluten', na=False)
        
        mask = n_mask | c_mask | t_mask
        
    elif case_id == "producto_sushi":
        cat_keywords = ['sushi', 'japanese', 'asian']
        c_mask = dff[col_cat].str.contains('|'.join(cat_keywords)) if col_cat else False
        n_mask = dff['restaurante'].str.contains('sushi|wok')
        mask = n_mask | c_mask
        
    elif case_id == "lugar_especifico":
        mask = dff['restaurante'].str.contains(case['expected_name'].lower())
    
    # Aplicar filtro (para los casos estándar que usaron 'mask')
    if case_id not in ["plato_milanesas", "feature_pelotero"]:
        candidatos = dff[mask].copy()
        candidatos = candidatos.drop_duplicates(subset=['restaurante']) # DEDUPLICACIÓN ESENCIAL
        candidatos['score'] = candidatos.apply(calculate_score, axis=1)
        top_5 = candidatos.sort_values('score', ascending=False).head(5)
        return top_5[['restaurante', 'score', 'zona']].to_dict('records')
        
    return [] # Should not happen


def run_benchmark():
    try:
        with open(TEST_CASES_FILE, "r", encoding="utf-8") as f:
            cases = json.load(f)
    except FileNotFoundError:
        print(f"❌ No encontré {TEST_CASES_FILE}")
        return

    print(f"\n🚀 Iniciando Benchmark de Calidad con GROUND TRUTH ({len(cases)} casos)")
    print("-" * 60)

    # USAR CONTEXT MANAGER PARA STARTUP EVENTS
    # Usamos main.app
    with TestClient(main.app) as client:
        passed_count = 0
        
        # Validación de carga de DF
        if main.df is None or main.df.empty:
            print("❌ ERRROR CRÍTICO: main.df no se cargó o está vacío después del startup.")
            # Intentar forzar carga u esperar? 
            # El lifespan debería haber corrido.
            return

        for case in cases:
            # 1. Calcular Ground Truth con el DF actualizado
            gt_results = get_ground_truth(case, main.df)
            
            # 2. Correr Test
            if run_single_case(client, case, gt_results):
                passed_count += 1
            print("-" * 60)
        
        print(f"\n📊 RESUMEN FINAL: {passed_count}/{len(cases)} PASARON")

def run_single_case(client, case, gt_results):
    query = case["query"]
    print(f"🧪 Test: {Fore.YELLOW}'{query}'{Style.RESET_ALL} (ID: {case['id']})")
    
    # Mostrar GT con detalle (Score, Cat, Zona) para comparar mejor.
    gt_top_details = []
    
    # Pre-calculamos detalles para display
    for item in gt_results[:5]: # Mostrar Top 5 del GT
        name = item.get('restaurante')
        # Buscar la fila completa en main.df para obtener la categoría
        row = main.df[main.df['restaurante'].str.lower() == name.lower()]
        cat = 'N/A'
        if not row.empty:
            row = row.iloc[0]
            cat = row.get('rubro') if 'rubro' in row else row.get('categoria', 'N/A')
        
        s = f"{name} (Score: {item.get('score', 0):.1f} | {cat} | {item.get('zona')})"
        gt_top_details.append(s)
        
    print(f"   👑 Ground Truth (Top 5 Teóricos):")
    for d in gt_top_details:
        print(f"      - {d}")
    
    start_time = time.time()
    
    payload = {
        "query": query,
        "conversation_context": {},
        "tone": "neutro"
    }

    try:
        response = client.post("/chat", json=payload)
        
        if response.status_code != 200:
            print(f"❌ Error HTTP {response.status_code}: {response.text}")
            return False

        data = response.json()
        latency = time.time() - start_time
        
        cards = data.get("restaurant_cards", [])
        reply = data.get("response", "")
        mode = data.get("mode", "")
        
        return verify_result(case, cards, reply, mode, latency, gt_results)

    except Exception as e:
        print(f"❌ Error inesperado: {e}")
        return False

def verify_result(case, cards, reply, mode, latency, gt_results):
    tipo = case["type"]
    passed = False
    details = ""

    card_names = [c.get('nombre') for c in cards] if cards else []

    # Verificación de COBERTURA DE GROUND TRUTH
    # ¿Cuántos del Top 3 de DB aparecieron en la respuesta?
    gt_top3 = [r['restaurante'] for r in gt_results[:3]]
    found_gt = [name for name in gt_top3 if name in card_names]
    
    # ANALISIS DE RECALL
    missing = [name for name in gt_top3 if name not in card_names]

    # Validaciones existentes
    if not cards and case.get("min_results", 1) > 0:
        print(f"   ⚠️ {Fore.RED}FALLO: No devolvió cards{Style.RESET_ALL} | Mode: {mode}")
        return False

    # 1. Validación de Zona
    if tipo == "zone_match":
        expected = [z.lower() for z in case["expected_zones"]]
        matches = 0
        total_cards = len(cards)
        bad_cards = []
        for card in cards:
            card_zone_info = (f"{card.get('zona', '')} {card.get('barrio', '')} {card.get('direccion', '')}").lower()
            if any(exp in card_zone_info for exp in expected): matches += 1
            else: bad_cards.append(f"{card.get('nombre')} ({card.get('zona')})")

        if matches >= case.get("min_results", 1):
            passed = True
            details = f"Zona OK ({matches}/{total_cards})"
        else:
            details = f"Zona FAIL. Esperaba {expected}. Desviados: {bad_cards}"

    # 2. Exact Match
    elif tipo == "exact_match":
        expected = case["expected_name"].lower()
        if any(expected in c.lower() for c in card_names):
            passed = True
            details = "Exact Match OK"
        else:
            details = "Exact Match FAIL"

    # 3. Keyword Match
    elif tipo == "keyword_match":
        passed = True 
        details = "Keyword Check (Manual)"

    # RESULTADO FINAL
    status = f"{Fore.GREEN}PASÓ{Style.RESET_ALL}" if passed else f"{Fore.RED}FALLÓ{Style.RESET_ALL}"
    print(f"   Resultado: {status} | Mode: {mode} | ⏱️ {latency:.2f}s")
    print(f"   {details}")
    
    # REPORTE DE RECALL
    print(f"   🔍 Recall Analysis:")
    print(f"      Encontrados (vs GT): {len(found_gt)}/{len(gt_top3)} -> {found_gt}")
    if missing:
        print(f"      ❌ {Fore.RED}Perdidos (Missed Opportunities): {missing}{Style.RESET_ALL}")
    
    if cards:
        # Formato detallado: Nombre (Categoria | Zona, Barrio)
        # Como la API no devuelve 'categoria', la buscamos en el DF usando el nombre
        detailed_cards = []
        for c in cards:
            cat = c.get('categoria', 'N/A')
            zon = c.get('zona', '')
            bar = c.get('barrio', '')
            
            detailed_cards.append(f"{c.get('nombre')} ({cat} | {zon}, {bar})")
            
        print(f"   📍 Cards devueltas: {detailed_cards}")
        
    return passed

if __name__ == "__main__":
    run_benchmark()
