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
    """|expected ∩ actual[:k]| / |expected|. Vacuamente 1.0 si no hay nada esperado."""
    if not expected:
        return 1.0
    top_k = set(actual[:k])
    hits = sum(1 for name in expected if name in top_k)
    return hits / len(expected)


def precision_at_k(expected: list, actual: list, k: int = 5) -> float:
    """|expected ∩ actual[:k]| / |actual[:k]|. Vacuamente 1.0 si no se esperaba ni se devolvió nada."""
    top_k = actual[:k]
    if not top_k:
        return 1.0 if not expected else 0.0
    expected_set = set(expected)
    hits = sum(1 for name in top_k if name in expected_set)
    return hits / len(top_k)


def mrr(expected: list, actual: list) -> float:
    """1/rank (1-indexado) del primer item esperado encontrado en actual. 0.0 si ninguno aparece."""
    if not expected:
        return 1.0
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


def evaluate_case(case: dict, actual_mode: str, actual_names: list, k: int = 5) -> dict:
    """
    Evalúa un único caso del golden dataset contra la respuesta real del backend.
    actual_names: lista de nombres de restaurantes devueltos (ya extraídos de restaurant_cards).
    """
    expected_restaurants = case.get("expected_restaurants", [])
    skipped = PLACEHOLDER in expected_restaurants

    expected_mode = case.get("expected_mode") or INTENT_TO_MODE.get(case.get("expected_intent", ""), "")

    result = {
        "id": case["id"],
        "query": case["query"],
        "expected_intent": case.get("expected_intent", ""),
        "skipped": skipped,
        "expected_mode": expected_mode,
        "actual_mode": actual_mode,
        "intent_ok": intent_match(expected_mode, actual_mode),
        "min_results_ok": len(actual_names) >= case.get("min_results", 1),
    }

    if skipped:
        result.update({"recall": None, "precision": None, "mrr": None, "passed": None})
        return result

    recall = recall_at_k(expected_restaurants, actual_names, k)
    precision = precision_at_k(expected_restaurants, actual_names, k)
    score_mrr = mrr(expected_restaurants, actual_names)

    passed = result["intent_ok"] and (recall >= 0.5 or not expected_restaurants)

    result.update({
        "recall": recall,
        "precision": precision,
        "mrr": score_mrr,
        "passed": passed,
    })
    return result


def aggregate(results: list) -> dict:
    """Resumen global + breakdown por expected_intent, ignorando casos SKIPPED."""
    scored = [r for r in results if not r["skipped"]]
    n_cases = len(scored)
    n_skipped = len(results) - n_cases

    if n_cases == 0:
        return {
            "n_cases": 0, "n_skipped": n_skipped,
            "avg_recall": 0.0, "avg_precision": 0.0, "avg_mrr": 0.0,
            "intent_accuracy": 0.0, "min_results_pass_rate": 0.0,
            "n_passed": 0, "by_type": {},
        }

    avg_recall = sum(r["recall"] for r in scored) / n_cases
    avg_precision = sum(r["precision"] for r in scored) / n_cases
    avg_mrr = sum(r["mrr"] for r in scored) / n_cases
    intent_accuracy = sum(1 for r in scored if r["intent_ok"]) / n_cases
    min_results_pass_rate = sum(1 for r in scored if r["min_results_ok"]) / n_cases
    n_passed = sum(1 for r in scored if r["passed"])

    by_type = {}
    types = {r["expected_intent"] for r in scored}
    for t in types:
        group = [r for r in scored if r["expected_intent"] == t]
        n = len(group)
        by_type[t] = {
            "n_cases": n,
            "avg_recall": sum(r["recall"] for r in group) / n,
            "avg_precision": sum(r["precision"] for r in group) / n,
            "avg_mrr": sum(r["mrr"] for r in group) / n,
            "intent_accuracy": sum(1 for r in group if r["intent_ok"]) / n,
        }

    return {
        "n_cases": n_cases,
        "n_skipped": n_skipped,
        "avg_recall": avg_recall,
        "avg_precision": avg_precision,
        "avg_mrr": avg_mrr,
        "intent_accuracy": intent_accuracy,
        "min_results_pass_rate": min_results_pass_rate,
        "n_passed": n_passed,
        "by_type": by_type,
    }
