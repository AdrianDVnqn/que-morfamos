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
# Texto con el que main.py responde cuando el LLM falla (main.py, except del CAMINO RAG).
FALLBACK_ERROR_TEXT = "Tuve un problema técnico"



_CACHE_EVIDENCIA = {}


def _evidencia_del_backend():
    """Mapa {nombre: resumen_reviews} y el matcher negacion-aware, tomados del modulo bajo test.

    Se cachea porque se llama una vez por caso y df_lugares no cambia durante la corrida.
    """
    if not _CACHE_EVIDENCIA:
        try:
            modulo = importlib.import_module(MODULE_NAME)
            df = getattr(modulo, "df_lugares", None)
            mapa = {}
            if df is not None and not df.empty and "resumen_reviews" in df.columns:
                mapa = {str(i): str(v or "") for i, v in df["resumen_reviews"].items()}
            _CACHE_EVIDENCIA["resumenes"] = mapa
            _CACHE_EVIDENCIA["mencion"] = getattr(modulo, "_mencion_positiva", None)
        except Exception as e:
            print(f"⚠️  No se pudo cargar la evidencia para las aserciones: {e}")
            _CACHE_EVIDENCIA["resumenes"] = {}
            _CACHE_EVIDENCIA["mencion"] = None
    return _CACHE_EVIDENCIA["resumenes"], _CACHE_EVIDENCIA["mencion"]


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

    # Los resumenes y el matcher con conciencia de negacion salen del backend, para que la
    # asercion por evidencia use exactamente el mismo criterio que el ranking (un resumen que
    # dice "NO tienen opciones veganas" no cuenta como evidencia a favor).
    resumenes, mencion = _evidencia_del_backend()
    result = evaluate_case(case, actual_mode, card_names, k=5, cards=cards,
                           resumenes=resumenes, mencion=mencion)
    # El backend atrapa los errores de LLM y responde 200 con un texto de fallback, así que un
    # corte de la API (ej. saldo agotado en DeepSeek) se vería como si TODOS los casos fallaran
    # a la vez. Sin esta marca, el modo estabilidad reporta eso como "±19 casos inestables"
    # cuando en realidad no hay nada inestable: la API estaba caída.
    result["api_error"] = reply.strip().startswith(FALLBACK_ERROR_TEXT)
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
    if case.get("expected_category"):
        color = Fore.GREEN if result.get("cat_ok") else Fore.RED
        print(f"   {color}Categoría: {result.get('cat_matched')}/{result.get('cat_total')} cards "
              f"son {case['expected_category']}{Style.RESET_ALL}")

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
        api_errors_por_corrida = []
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
            n_api_err = sum(1 for r in results if r.get("api_error"))
            if n_api_err:
                api_errors_por_corrida.append((corrida + 1, n_api_err))
            if REPEATS > 1:
                aviso = f" {Fore.RED}[{n_api_err} casos con la API caída]{Style.RESET_ALL}" if n_api_err else ""
                print(f"   Corrida {corrida + 1}: {corrida_summary['n_passed']}/{corrida_summary['n_cases']} PASARON{aviso}")

        summary = aggregate(results)  # última corrida, para el detalle de métricas

        def fmt(v):
            return f"{v:.2f}" if v is not None else "n/a"

        # La línea "📊 RESUMEN FINAL: X/Y PASARON" es prefijo fijo: analyze_benchmark_logs.py la parsea.
        print(f"\n📊 RESUMEN FINAL: {summary['n_passed']}/{summary['n_cases']} PASARON "
              f"({summary['n_skipped']} sin curar) | "
              f"Recall@5={fmt(summary['avg_recall'])} | Precision@5={fmt(summary['avg_precision'])} | "
              f"MRR={fmt(summary['avg_mrr'])} | Intent Accuracy={summary['intent_accuracy']:.2f}")
        print(f"   ↳ Retrieval medido sobre {summary['n_retrieval']}/{summary['n_cases']} casos "
              f"(los demás son de ruteo puro o se juzgan por evidencia, y no aportan al promedio).")
        # Las consultas de concepto no se miden contra nombres: se reportan aparte, con cuántos
        # de los lugares devueltos cumplen de verdad lo que la consulta pedía.
        if summary.get("n_evidencia"):
            ratio = summary.get("ev_ratio")
            print(f"   ↳ Evidencia: {summary['ev_cumplen']}/{summary['ev_total']} lugares devueltos "
                  f"cumplen los conceptos pedidos ({ratio:.2f}) sobre {summary['n_evidencia']} "
                  f"consultas de concepto.")

        if summary["by_type"]:
            print("\n📋 Breakdown por intención:")
            for intent, stats in summary["by_type"].items():
                print(f"   {intent}: n={stats['n_cases']} (retrieval: {stats['n_retrieval']}) | "
                      f"Recall={fmt(stats['avg_recall'])} | Precision={fmt(stats['avg_precision'])} | "
                      f"MRR={fmt(stats['avg_mrr'])} | IntentAcc={stats['intent_accuracy']:.2f}")

        if REPEATS > 1:
            print(f"\n   Pass count por corrida: {pass_counts} "
                  f"(min {min(pass_counts)}, max {max(pass_counts)})")
            if api_errors_por_corrida:
                print(f"\n   {Fore.RED}🚨 LA API DEL LLM FALLÓ DURANTE LA MEDICIÓN{Style.RESET_ALL}")
                for corrida, n in api_errors_por_corrida:
                    print(f"      Corrida {corrida}: {n} casos respondieron con el texto de fallback de error.")
                print(f"   {Fore.RED}El reporte de estabilidad de abajo NO es confiable: los casos que "
                      f"'fallaron' pueden haber fallado por la API caída, no por inestabilidad real. "
                      f"Revisá saldo/credenciales del proveedor y volvé a correr.{Style.RESET_ALL}")
            print_flakiness(historial, REPEATS)


if __name__ == "__main__":
    run_benchmark()
