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

    result = evaluate_case(case, actual_mode, card_names, k=5)
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
    print(f"   Recall@5: {result['recall']:.2f} | Precision@5: {result['precision']:.2f} | MRR: {result['mrr']:.2f}")

    if not result["intent_ok"]:
        print(f"   {Fore.RED}⚠️ Intent/mode mismatch{Style.RESET_ALL}")

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


def run_benchmark():
    try:
        with open(GOLDEN_FILE, "r", encoding="utf-8") as f:
            cases = json.load(f)
    except FileNotFoundError:
        print(f"❌ No encontré {GOLDEN_FILE}")
        return

    print(f"\n🚀 Iniciando Golden Dataset Eval ({len(cases)} casos){' | Judge de calidad ACTIVADO' if WITH_JUDGE else ''}")
    print("-" * 60)

    with TestClient(main.app) as client:
        results = []
        for case in cases:
            result = run_single_case(client, case)
            print_case_result(case, result)
            print("-" * 60)
            if result is not None:
                results.append(result)

        summary = aggregate(results)

        print(f"\n📊 RESUMEN FINAL: {summary['n_passed']}/{summary['n_cases']} PASARON "
              f"({summary['n_skipped']} sin curar) | "
              f"Recall@5={summary['avg_recall']:.2f} | Precision@5={summary['avg_precision']:.2f} | "
              f"MRR={summary['avg_mrr']:.2f} | Intent Accuracy={summary['intent_accuracy']:.2f}")

        if summary["by_type"]:
            print("\n📋 Breakdown por intención:")
            for intent, stats in summary["by_type"].items():
                print(f"   {intent}: n={stats['n_cases']} | Recall={stats['avg_recall']:.2f} | "
                      f"Precision={stats['avg_precision']:.2f} | MRR={stats['avg_mrr']:.2f} | "
                      f"IntentAcc={stats['intent_accuracy']:.2f}")


if __name__ == "__main__":
    run_benchmark()
