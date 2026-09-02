"""
Métricas puras para evaluar el golden dataset del agente RAG.
Sin I/O, sin dependencias de main.py: solo listas/dicts adentro, dicts afuera.
"""

# Mapeo de referencia intención LLM -> mode real del endpoint /chat.
# NO es 1:1: FOLLOWUP y BLOCK (con contexto fresco) colapsan a "general".
# Cada caso del golden dataset ya trae su propio expected_mode resuelto;
# esta tabla queda como documentación / y como fallback si un caso no lo trae.
INTENT_TO_MODE = {
    "RECOMMENDATION": "rag",
    "SPECIFIC": "resumen",
    "STATS": "estadisticas",
    "FOLLOWUP": "general",
    "BLOCK": "general",
}

PLACEHOLDER = "<TODO: curate>"


def recall_at_k(expected: list, actual: list, k: int = 5) -> float:
    """|expected ∩ actual[:k]| / |expected|. None si no hay ground truth que medir."""
    if not expected:
        return None
    top_k = set(actual[:k])
    hits = sum(1 for name in expected if name in top_k)
    return hits / len(expected)


def precision_at_k(expected: list, actual: list, k: int = 5) -> float:
    """|expected ∩ actual[:k]| / |actual[:k]|. None si no hay ground truth que medir."""
    if not expected:
        return None
    top_k = actual[:k]
    if not top_k:
        return 0.0
    expected_set = set(expected)
    hits = sum(1 for name in top_k if name in expected_set)
    return hits / len(top_k)


def mrr(expected: list, actual: list) -> float:
    """1/rank (1-indexado) del primer item esperado encontrado en actual. None si no hay ground truth."""
    if not expected:
        return None
    expected_set = set(expected)
    for i, name in enumerate(actual, start=1):
        if name in expected_set:
            return 1.0 / i
    return 0.0


def intent_match(expected_mode: str, actual_mode: str) -> bool:
    """Compara directo contra el expected_mode ya resuelto en el caso."""
    return expected_mode == actual_mode


def zone_match(expected_zones: list, cards: list) -> tuple:
    """
    Chequea si las cards devueltas caen dentro de las zonas esperadas.
    cards: lista de dicts con claves 'zona', 'barrio', 'direccion' (formato RestaurantCard).
    Retorna (passed, matched_count, total_cards).
    """
    if not expected_zones:
        return True, 0, len(cards)

    expected_lower = [z.lower() for z in expected_zones]
    matched = 0
    for card in cards:
        info = f"{card.get('zona', '')} {card.get('barrio', '')} {card.get('direccion', '')}".lower()
        if any(exp in info for exp in expected_lower):
            matched += 1

    total = len(cards)
    passed = matched >= min(1, total) if total else False
    return passed, matched, total


def category_match(expected_categories: list, cards: list, k: int = 5, min_ratio: float = 0.6) -> tuple:
    """
    Chequea qué fracción de las cards devueltas cae en alguna de las categorías esperadas.

    Para queries de categoría ("mejores pizzerías") una lista curada de nombres es el modelo de
    evaluación equivocado: hay 52 pizzerías en la base y el ground truth elige 5, así que el
    sistema puede devolver 5 pizzerías legítimas y sacar recall 0.20. Lo que importa realmente
    es si lo que devolvió ES de la categoría pedida, no si adivinó una lista subjetiva.

    Retorna (passed, matched, total).
    """
    if not expected_categories:
        return True, 0, len(cards or [])

    top_k = (cards or [])[:k]
    if not top_k:
        return False, 0, 0

    patrones = [c.lower() for c in expected_categories]
    matched = sum(
        1 for card in top_k
        if any(p in str(card.get("categoria", "")).lower() for p in patrones)
    )
    return (matched / len(top_k)) >= min_ratio, matched, len(top_k)


UMBRAL_RESENAS = 2
"""Cuantas resenas distintas tienen que confirmar un concepto para darlo por cumplido.

Dos y no una: una mencion suelta puede ser ironica, equivocada o vieja. Es el mismo criterio con
el que se derivo el ground truth el 01-sep.
"""


def evidence_match(expected_evidence, actual_names, indice, grupos, k=5, umbral=UMBRAL_RESENAS):
    """De los lugares devueltos, cuantos cumplen DE VERDAD lo que la consulta pidio.

    La vara son las RESENAS, no el resumen. `indice` es {(nombre, i_grupo): n_menciones} y
    `grupos` mapea cada grupo de terminos a su indice; los arma `run_benchmark` con una consulta.

    Por que no el resumen: es lo que estamos evaluando, asi que no puede ser tambien el juez.
    Medido el 01-sep, la columna v3 daba 0.77 verificada contra si misma y 0.57 contra una vara
    fija — la metrica se inflaba con solo agregar texto al campo. Con las resenas como vara, la
    evidencia es invariante a que resumen se use y sirve para comparar variantes entre si.

    Un lugar cumple si TODOS los conceptos de la consulta tienen >= `umbral` resenas que los
    confirmen.
    """
    if not expected_evidence:
        return 0, 0, 0
    considerados = actual_names[:k]
    if not considerados:
        return 0, 0, 0
    cumplen = 0
    for nombre in considerados:
        ok = True
        for grupo in expected_evidence:
            gi = (grupos or {}).get(tuple(grupo))
            if gi is None or (indice or {}).get((nombre, gi), 0) < umbral:
                ok = False
                break
        cumplen += 1 if ok else 0
    return cumplen, cumplen, len(considerados)


def summary_coverage(expected_evidence, actual_names, indice, grupos, resumenes, k=5,
                     mencion=None, umbral=UMBRAL_RESENAS):
    """De los lugares que las RESENAS confirman, en cuantos el RESUMEN tambien lo dice.

    Es la metrica que evalua la capa de resumen, separada de la que evalua al ranking. Sale casi
    gratis porque el indice de resenas ya esta armado.

    Medido a mano el 01-sep sobre una muestra dirigida, el resumen de produccion captura el 33%
    de las features que las resenas confirman, y el prompt v5.1 el 51%. Tener el numero adentro
    del benchmark es lo que permite evaluar un prompt nuevo sin tocar la metrica del ranking.
    """
    if not expected_evidence or not resumenes:
        return 0, 0
    check = mencion or (lambda t, term: term.lower() in (t or "").lower())
    confirmados = cubiertos = 0
    for nombre in actual_names[:k]:
        for grupo in expected_evidence:
            gi = (grupos or {}).get(tuple(grupo))
            if gi is None or (indice or {}).get((nombre, gi), 0) < umbral:
                continue  # las resenas no lo confirman: no hay nada que el resumen deba reflejar
            confirmados += 1
            if any(check(resumenes.get(nombre, ""), t) for t in grupo):
                cubiertos += 1
    return cubiertos, confirmados


def evaluate_case(case: dict, actual_mode: str, actual_names: list, k: int = 5, cards: list = None,
                  resumenes: dict = None, mencion=None, indice: dict = None,
                  grupos: dict = None) -> dict:
    """
    Evalúa un único caso del golden dataset contra la respuesta real del backend.
    actual_names: lista de nombres de restaurantes devueltos (ya extraídos de restaurant_cards).
    cards: los RestaurantCard completos, necesarios para chequear zona.
    """
    expected_restaurants = case.get("expected_restaurants", [])
    skipped = PLACEHOLDER in expected_restaurants
    expected_empty = case.get("expected_empty", False)

    expected_mode = case.get("expected_mode") or INTENT_TO_MODE.get(case.get("expected_intent", ""), "")

    # Un caso mide retrieval solo si tiene ground truth que comparar. Los demás (stats, block,
    # followup) miden ruteo: mezclarlos en el promedio de recall regala 1.0 y infla el titular.
    # Un caso juzgado por evidencia NO mide retrieval contra nombres: su `expected_restaurants`
    # quedo como referencia informativa y promediarlo ensucia el titular con una medicion que ya
    # no gatea nada. Se lo saca del scoreboard de recall/precision y se lo reporta aparte.
    mide_evidencia = bool(case.get("expected_evidence"))
    mide_retrieval = (bool(expected_restaurants) or expected_empty) and not mide_evidencia

    result = {
        "id": case["id"],
        "query": case["query"],
        "expected_intent": case.get("expected_intent", ""),
        "skipped": skipped,
        "expected_mode": expected_mode,
        "actual_mode": actual_mode,
        "intent_ok": intent_match(expected_mode, actual_mode),
        "min_results_ok": len(actual_names) >= case.get("min_results", 1),
        "mide_retrieval": mide_retrieval,
        "mide_evidencia": mide_evidencia,
        "expected_empty": expected_empty,
        "open_ended": bool(case.get("open_ended")),
    }

    if skipped:
        result.update({"recall": None, "precision": None, "mrr": None, "passed": None})
        return result

    recall = recall_at_k(expected_restaurants, actual_names, k)
    precision = precision_at_k(expected_restaurants, actual_names, k)
    score_mrr = mrr(expected_restaurants, actual_names)
    cat_ok, cat_matched, cat_total = category_match(case.get("expected_category", []), cards or [], k)
    ev_cumplen, _, ev_total = evidence_match(
        case.get("expected_evidence", []), actual_names, indice, grupos, k
    )
    ev_ratio = (ev_cumplen / ev_total) if ev_total else None
    cob_cubiertos, cob_total = summary_coverage(
        case.get("expected_evidence", []), actual_names, indice, grupos, resumenes, k, mencion
    )

    if expected_empty:
        # Caso que debe NO devolver resultados. Antes esto era un pass automático (la condición
        # `not expected_restaurants` cortocircuitaba `passed`), así que el único test de "no
        # encontré nada" era incapaz de fallar aunque el RAG devolviera 5 lugares reales.
        retrieval_ok = len(actual_names) == 0
    elif case.get("expected_evidence"):
        # Query de concepto: se juzga por la EVIDENCIA de lo devuelto, no contra una lista
        # subjetiva de nombres. El umbral es 0.6 —3 de 5— y no 1.0 a proposito: el backend
        # devuelve 3 exactos + 2 relacionados, y los relacionados por definicion pueden no
        # cumplir todo.
        retrieval_ok = (ev_ratio or 0) >= 0.6 and result["min_results_ok"]
    elif case.get("expected_category"):
        # Query de categoría: se juzga por la categoría de lo devuelto, no contra una lista
        # subjetiva de nombres (ver category_match).
        retrieval_ok = cat_ok and result["min_results_ok"]
    elif expected_restaurants and case.get("open_ended"):
        # Query abierta: hay muchos más lugares válidos que los que el sistema puede devolver
        # (el backend corta en 5 cards: 3 exactos + 2 relacionados), así que expected_restaurants
        # es una MUESTRA de respuestas aceptables, no la lista exhaustiva. Exigir recall contra
        # una muestra arbitraria castiga al sistema por una decisión de curación: con 8 esperados
        # y 5 slots, el recall máximo es 0.62 aunque acierte los 5. Acá lo que importa es la
        # precisión: ¿los que devolvió salen del conjunto aceptable?
        retrieval_ok = precision >= 0.6 and result["min_results_ok"]
    elif expected_restaurants:
        retrieval_ok = recall >= 0.5 and precision >= 0.4 and result["min_results_ok"]
    else:
        retrieval_ok = True  # caso de ruteo puro: no hay nada que exigirle al retrieval

    # Chequeo de zona (antes zone_match estaba definida pero nunca se llamaba desde ningún lado,
    # así que expected_zones no se evaluaba: la detección de zona quedaba sin testear).
    zona_ok, zona_matched, zona_total = zone_match(case.get("expected_zones", []), cards or [])

    passed = result["intent_ok"] and retrieval_ok and zona_ok

    result.update({
        "recall": None if mide_evidencia else recall,
        "precision": None if mide_evidencia else precision,
        "mrr": None if mide_evidencia else score_mrr,
        "recall_informativo": recall,
        "retrieval_ok": retrieval_ok,
        "zona_ok": zona_ok,
        "zona_matched": zona_matched,
        "zona_total": zona_total,
        "cat_ok": cat_ok,
        "cat_matched": cat_matched,
        "cat_total": cat_total,
        "ev_cumplen": ev_cumplen,
        "ev_total": ev_total,
        "ev_ratio": ev_ratio,
        "cob_cubiertos": cob_cubiertos,
        "cob_total": cob_total,
        "passed": passed,
    })
    return result


def _promedio(valores):
    """Promedio ignorando None (casos sin ground truth). None si no queda nada que promediar."""
    reales = [v for v in valores if v is not None]
    return sum(reales) / len(reales) if reales else None


def aggregate(results: list) -> dict:
    """Resumen separado en dos scoreboards + breakdown por expected_intent, ignorando SKIPPED.

    Las métricas de retrieval se promedian SOLO sobre los casos que tienen ground truth real.
    Antes se promediaban sobre los 22 casos, y los que no tienen nada esperado (stats, block,
    followup) aportaban un 1.0 vacuo que inflaba el titular ~40%.
    """
    scored = [r for r in results if not r["skipped"]]
    n_cases = len(scored)
    n_skipped = len(results) - n_cases

    if n_cases == 0:
        return {
            "n_cases": 0, "n_skipped": n_skipped, "n_retrieval": 0,
            "avg_recall": None, "avg_precision": None, "avg_mrr": None,
            "intent_accuracy": 0.0, "min_results_pass_rate": 0.0,
            "n_passed": 0, "by_type": {},
        }

    retrieval = [r for r in scored if r.get("mide_retrieval")]
    evidencia = [r for r in scored if r.get("mide_evidencia")]
    ev_cumplen = sum(r.get("ev_cumplen") or 0 for r in evidencia)
    ev_total = sum(r.get("ev_total") or 0 for r in evidencia)
    cob_cubiertos = sum(r.get("cob_cubiertos") or 0 for r in evidencia)
    cob_total = sum(r.get("cob_total") or 0 for r in evidencia)

    intent_accuracy = sum(1 for r in scored if r["intent_ok"]) / n_cases
    min_results_pass_rate = sum(1 for r in scored if r["min_results_ok"]) / n_cases
    n_passed = sum(1 for r in scored if r["passed"])

    by_type = {}
    for t in {r["expected_intent"] for r in scored}:
        group = [r for r in scored if r["expected_intent"] == t]
        by_type[t] = {
            "n_cases": len(group),
            "n_retrieval": sum(1 for r in group if r.get("mide_retrieval")),
            "avg_recall": _promedio([r["recall"] for r in group]),
            "avg_precision": _promedio([r["precision"] for r in group]),
            "avg_mrr": _promedio([r["mrr"] for r in group]),
            "intent_accuracy": sum(1 for r in group if r["intent_ok"]) / len(group),
        }

    return {
        "n_evidencia": len(evidencia),
        "ev_cumplen": ev_cumplen,
        "ev_total": ev_total,
        "ev_ratio": (ev_cumplen / ev_total) if ev_total else None,
        "cob_cubiertos": cob_cubiertos,
        "cob_total": cob_total,
        "cob_ratio": (cob_cubiertos / cob_total) if cob_total else None,
        "n_cases": n_cases,
        "n_skipped": n_skipped,
        "n_retrieval": len(retrieval),
        "avg_recall": _promedio([r["recall"] for r in scored]),
        "avg_precision": _promedio([r["precision"] for r in scored]),
        "avg_mrr": _promedio([r["mrr"] for r in scored]),
        "intent_accuracy": intent_accuracy,
        "min_results_pass_rate": min_results_pass_rate,
        "n_passed": n_passed,
        "by_type": by_type,
    }
