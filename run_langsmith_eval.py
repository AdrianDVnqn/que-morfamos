"""
Corre el golden dataset como un experimento trackeado en LangSmith.
Complementa a run_benchmark.py (rápido, local, sin tracking) con historial
comparable entre corridas: recall/precision/MRR/intent accuracy por caso,
más un juez de calidad opcional, quedan versionados en la UI de LangSmith.

Uso:
    python run_langsmith_eval.py                    # local, contra main.py
    TARGET_URL=https://<fly-app>.fly.dev python run_langsmith_eval.py   # contra producción
    WITH_JUDGE=1 python run_langsmith_eval.py        # + juez de calidad de respuesta
    BENCHMARK_MODULE=main_experimental python run_langsmith_eval.py     # A/B local
"""
import json
import os
import importlib

import requests
from langsmith import Client, evaluate
from fastapi.testclient import TestClient

from eval_metrics import recall_at_k, precision_at_k, mrr, intent_match, PLACEHOLDER
from judge import judge_answer_quality

DATASET_NAME = os.getenv("LANGSMITH_DATASET", "que-morfamos-golden")
TARGET_URL = os.getenv("TARGET_URL")  # si está seteada, pega contra esa URL (ej. prod); si no, local
MODULE_NAME = os.getenv("BENCHMARK_MODULE", "main")
WITH_JUDGE = os.getenv("WITH_JUDGE", "0") == "1"
GOLDEN_FILE = "golden_dataset.json"


def load_cases():
    with open(GOLDEN_FILE, encoding="utf-8") as f:
        cases = json.load(f)
    curated = [c for c in cases if PLACEHOLDER not in c.get("expected_restaurants", [])]
    skipped = len(cases) - len(curated)
    if skipped:
        print(f"⏭️  {skipped} caso(s) sin curar, excluidos del dataset de LangSmith.")
    return curated


def sync_dataset(cases, client):
    """Resync completo: borra los examples existentes del dataset y sube los actuales.
    Simple y correcto a esta escala (~20 casos); evita lógica de diff innecesaria."""
    existing = list(client.list_datasets(dataset_name=DATASET_NAME))
    if existing:
        dataset = existing[0]
        for ex in client.list_examples(dataset_id=dataset.id):
            client.delete_example(ex.id)
        print(f"🔄 Dataset '{DATASET_NAME}' existente: examples anteriores borrados.")
    else:
        dataset = client.create_dataset(
            dataset_name=DATASET_NAME,
            description="Golden dataset del agente RAG de Qué Morfamos (retrieval + intent).",
        )
        print(f"🆕 Dataset '{DATASET_NAME}' creado.")

    examples = [
        {
            "inputs": {
                "query": c["query"],
                "conversation_context": c.get("conversation_context", {}),
                "tone": c.get("tone", "neutro"),
            },
            "outputs": {
                "expected_restaurants": c.get("expected_restaurants", []),
                "expected_mode": c.get("expected_mode"),
                "min_results": c.get("min_results", 1),
            },
            "metadata": {
                "id": c["id"],
                "expected_intent": c.get("expected_intent"),
                "notes": c.get("notes", ""),
            },
        }
        for c in cases
    ]
    client.create_examples(dataset_id=dataset.id, examples=examples)
    print(f"✅ {len(examples)} examples sincronizados en LangSmith.")
    return dataset


def make_target(backend):
    """backend: TestClient local o string de URL (ej. producción en Fly.io)."""

    def target(inputs: dict) -> dict:
        payload = {
            "query": inputs["query"],
            "conversation_context": inputs.get("conversation_context", {}),
            "tone": inputs.get("tone", "neutro"),
        }
        if isinstance(backend, str):
            data = requests.post(f"{backend}/chat", json=payload, timeout=120).json()
        else:
            data = backend.post("/chat", json=payload).json()
        cards = data.get("restaurant_cards", []) or []
        return {
            "mode": data.get("mode", ""),
            "restaurant_names": [c.get("nombre") for c in cards],
            "response": data.get("response", ""),
        }

    return target


# --- Evaluadores: wrappers finitos sobre eval_metrics.py, firma (inputs, outputs, reference_outputs) ---

def recall_evaluator(inputs, outputs, reference_outputs) -> float:
    return recall_at_k(reference_outputs.get("expected_restaurants", []), outputs.get("restaurant_names", []), k=5)


def precision_evaluator(inputs, outputs, reference_outputs) -> float:
    return precision_at_k(reference_outputs.get("expected_restaurants", []), outputs.get("restaurant_names", []), k=5)


def mrr_evaluator(inputs, outputs, reference_outputs) -> float:
    return mrr(reference_outputs.get("expected_restaurants", []), outputs.get("restaurant_names", []))


def intent_evaluator(inputs, outputs, reference_outputs) -> bool:
    return intent_match(reference_outputs.get("expected_mode", ""), outputs.get("mode", ""))


def make_judge_evaluator(llm_smart):
    def judge_evaluator(inputs, outputs, reference_outputs) -> dict:
        """Evaluador multi-métrica: LangSmith espera {"results": [{"key":..., "score":...}, ...]}
        para que un solo evaluador emita varios feedbacks (uno por métrica del juez)."""
        result = judge_answer_quality(
            llm_smart,
            inputs.get("query", ""),
            outputs.get("response", ""),
            outputs.get("restaurant_names", []),
            reference_outputs.get("expected_restaurants", []),
        )
        if "error" in result:
            return {"results": [{"key": "judge_error", "comment": result["error"]}]}
        return {
            "results": [
                {"key": "judge_relevancia", "score": result.get("relevancia")},
                {"key": "judge_fidelidad_ok", "score": result.get("fidelidad_ok")},
                {"key": "judge_tono_ok", "score": result.get("tono_ok")},
                {"key": "judge_alucinacion_detalle", "comment": result.get("alucinacion_detalle", "")},
            ]
        }

    return judge_evaluator


def run():
    print("⏳ Cargando aplicación backend (modelos, DB)...")
    main = importlib.import_module(MODULE_NAME)
    print(f"🎯 Módulo backend: {MODULE_NAME} | Target: {TARGET_URL or 'local (TestClient)'}")

    client = Client()  # lee LANGSMITH_API_KEY / LANGSMITH_PROJECT del entorno
    cases = load_cases()

    with TestClient(main.app) as tc:  # siempre se abre: inicializa llm_smart/df_lugares
        sync_dataset(cases, client)

        backend = TARGET_URL if TARGET_URL else tc
        evaluators = [recall_evaluator, precision_evaluator, mrr_evaluator, intent_evaluator]
        if WITH_JUDGE:
            evaluators.append(make_judge_evaluator(main.llm_smart))
            print("🧑‍⚖️ Juez de calidad ACTIVADO")

        results = evaluate(
            make_target(backend),
            data=DATASET_NAME,
            evaluators=evaluators,
            client=client,
            experiment_prefix=f"golden-{'prod' if TARGET_URL else 'local'}-{MODULE_NAME}",
            max_concurrency=4,
        )

    print(f"\n✅ Experimento corrido. Revisá los resultados en la UI de LangSmith (proyecto: "
          f"{os.getenv('LANGSMITH_PROJECT', 'default')}, dataset: {DATASET_NAME}).")
    return results


if __name__ == "__main__":
    run()
