import json
import time
import os
import warnings
import importlib
from colorama import Fore, Style, init

from fastapi.testclient import TestClient

from eval_metrics import evaluate_case, aggregate, PLACEHOLDER
from judge import judge_answer_quality

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)

print("⏳ Cargando aplicación backend (modelos, DB)...")

MODULE_NAME = os.getenv("BENCHMARK_MODULE", "main")
print(f"🎯 Usando módulo backend: {MODULE_NAME}")
main = importlib.import_module(MODULE_NAME)

init()

GOLDEN_FILE = "golden_dataset.json"
WITH_JUDGE = os.getenv("WITH_JUDGE", "0") == "1"
# El Juez LLM es la única llamada no cacheada del camino crítico y, pese a temperature=0, no es
# determinística en la práctica (DeepSeek/MoE). Su salida define exactos[:3], o sea las cards, o
# sea las métricas: el pass count osciló 20/19/19/18 con código idéntico. Con REPEATS>1 el
# benchmark corre la suite N veces y reporta qué casos son inestables, para no confundir ruido
# con una mejora real.
REPEATS = int(os.getenv("BENCHMARK_REPEATS", "1"))


def run_single_case(client, case):
    payload = {
        "query": case["query"],
        "conversation_context": case.get("conversation_context", {}),
        "tone": case.get("tone", "neutro"),
    }

    start_time = time.time()
    try:
        response = client.post("/chat", json=payload)
        latency = time.time() - start_time
    except Exception as e:
        print(f"❌ Error inesperado: {e}")
        return None

    if response.status_code != 200:
        print(f"❌ Error HTTP {response.status_code}: {response.text}")
        return None

    data = response.json()
    cards = data.get("restaurant_cards", []) or []
    card_names = [c.get("nombre") for c in cards]
    reply = data.get("response", "")
    actual_mode = data.get("mode", "")

    result = evaluate_case(case, actual_mode, card_names, k=5, cards=cards)
    result["latency"] = latency
    result["cards"] = cards
    result["reply"] = reply

    if WITH_JUDGE and not result["skipped"] and case.get("judge_eligible", True):
        result["judge"] = judge_answer_quality(
            main.llm_smart, case["query"], reply, card_names, case.get("expected_restaurants", [])
        )

    return result


def print_case_result(case, result):
    query = case["query"]
    print(f"🧪 Test: {Fore.YELLOW}'{query}'{Style.RESET_ALL} (ID: {case['id']})")

    if result is None:
        print(f"   {Fore.RED}FALLÓ: error de request{Style.RESET_ALL}")
        return

    if result["skipped"]:
        print(f"   {Fore.CYAN}⏭️  SKIPPED (sin curar: expected_restaurants tiene '{PLACEHOLDER}'){Style.RESET_ALL}")
        return

    status = f"{Fore.GREEN}PASÓ{Style.RESET_ALL}" if result["passed"] else f"{Fore.RED}FALLÓ{Style.RESET_ALL}"
    print(f"   Resultado: {status} | Mode: {result['actual_mode']} (esperado: {result['expected_mode']}) | ⏱️ {result['latency']:.2f}s")

    if result["recall"] is None:
        etiqueta = "esperaba CERO resultados" if result.get("expected_empty") else "caso de ruteo (sin ground truth de retrieval)"
        print(f"   {Fore.CYAN}Sin métricas de retrieval: {etiqueta}{Style.RESET_ALL}")
    else:
        print(f"   Recall@5: {result['recall']:.2f} | Precision@5: {result['precision']:.2f} | MRR: {result['mrr']:.2f}")

    if not result["intent_ok"]:
        print(f"   {Fore.RED}⚠️ Intent/mode mismatch{Style.RESET_ALL}")
    if result.get("expected_empty") and not result.get("retrieval_ok"):
        print(f"   {Fore.RED}⚠️ Debía no devolver resultados y devolvió {len(result['cards'])}{Style.RESET_ALL}")
    if not result.get("zona_ok", True):
        print(f"   {Fore.RED}⚠️ Zona: 0/{result.get('zona_total')} cards caen en las zonas esperadas{Style.RESET_ALL}")

    if result["cards"]:
        names = [c.get("nombre") for c in result["cards"]]
        print(f"   📍 Cards devueltas: {names}")

    if "judge" in result:
        j = result["judge"]
        if "error" in j:
            print(f"   {Fore.RED}Juez de calidad: error ({j['error']}){Style.RESET_ALL}")
        else:
            fidelidad = f"{Fore.GREEN}OK{Style.RESET_ALL}" if j.get("fidelidad_ok") else f"{Fore.RED}ALUCINÓ{Style.RESET_ALL}"
            print(f"   🧑‍⚖️ Juez calidad: Relevancia={j.get('relevancia')}/5 | Fidelidad={fidelidad} | Tono OK={j.get('tono_ok')}")
            if not j.get("fidelidad_ok"):
                print(f"      {Fore.RED}{j.get('alucinacion_detalle', '')}{Style.RESET_ALL}")


def print_flakiness(historial, n_corridas):
    """Reporta qué casos no dieron el mismo resultado en todas las corridas."""
    inestables = {cid: res for cid, res in historial.items() if len(set(res)) > 1}
    print(f"\n🎲 Estabilidad sobre {n_corridas} corridas:")
    if not inestables:
        print(f"   {Fore.GREEN}Todos los casos dieron el mismo resultado en las {n_corridas} corridas.{Style.RESET_ALL}")
        return
    print(f"   {Fore.YELLOW}{len(inestables)} caso(s) INESTABLE(S) — su resultado cambia entre corridas "
          f"sin que cambie el código:{Style.RESET_ALL}")
    for cid, res in sorted(inestables.items()):
        pasadas = sum(1 for x in res if x)
        print(f"   - {cid}: pasó {pasadas}/{n_corridas} veces")
    print(f"   {Fore.YELLOW}⚠️ Piso de ruido: ±{len(inestables)} casos. Una mejora menor a eso "
          f"no es distinguible del azar.{Style.RESET_ALL}")


def run_benchmark():
    try:
        with open(GOLDEN_FILE, "r", encoding="utf-8") as f:
            cases = json.load(f)
    except FileNotFoundError:
        print(f"❌ No encontré {GOLDEN_FILE}")
        return

    print(f"\n🚀 Iniciando Golden Dataset Eval ({len(cases)} casos)"
          f"{' | Judge de calidad ACTIVADO' if WITH_JUDGE else ''}"
          f"{f' | {REPEATS} corridas (modo estabilidad)' if REPEATS > 1 else ''}")
    print("-" * 60)

    with TestClient(main.app) as client:
        historial = {}
        pass_counts = []
        results = []

        for corrida in range(REPEATS):
            if REPEATS > 1:
                print(f"\n{Fore.CYAN}=== CORRIDA {corrida + 1}/{REPEATS} ==={Style.RESET_ALL}")
            results = []
            for case in cases:
                result = run_single_case(client, case)
                if REPEATS == 1:
                    print_case_result(case, result)
                    print("-" * 60)
                if result is not None:
                    results.append(result)
                    if not result["skipped"]:
                        historial.setdefault(case["id"], []).append(bool(result["passed"]))
            corrida_summary = aggregate(results)
            pass_counts.append(corrida_summary["n_passed"])
            if REPEATS > 1:
                print(f"   Corrida {corrida + 1}: {corrida_summary['n_passed']}/{corrida_summary['n_cases']} PASARON")

        summary = aggregate(results)  # última corrida, para el detalle de métricas

        def fmt(v):
            return f"{v:.2f}" if v is not None else "n/a"

        # La línea "📊 RESUMEN FINAL: X/Y PASARON" es prefijo fijo: analyze_benchmark_logs.py la parsea.
        print(f"\n📊 RESUMEN FINAL: {summary['n_passed']}/{summary['n_cases']} PASARON "
              f"({summary['n_skipped']} sin curar) | "
              f"Recall@5={fmt(summary['avg_recall'])} | Precision@5={fmt(summary['avg_precision'])} | "
              f"MRR={fmt(summary['avg_mrr'])} | Intent Accuracy={summary['intent_accuracy']:.2f}")
        print(f"   ↳ Retrieval medido sobre {summary['n_retrieval']}/{summary['n_cases']} casos "
              f"(los demás son de ruteo puro y no aportan al promedio).")

        if summary["by_type"]:
            print("\n📋 Breakdown por intención:")
            for intent, stats in summary["by_type"].items():
                print(f"   {intent}: n={stats['n_cases']} (retrieval: {stats['n_retrieval']}) | "
                      f"Recall={fmt(stats['avg_recall'])} | Precision={fmt(stats['avg_precision'])} | "
                      f"MRR={fmt(stats['avg_mrr'])} | IntentAcc={stats['intent_accuracy']:.2f}")

        if REPEATS > 1:
            print(f"\n   Pass count por corrida: {pass_counts} "
                  f"(min {min(pass_counts)}, max {max(pass_counts)})")
            print_flakiness(historial, REPEATS)


if __name__ == "__main__":
    run_benchmark()
