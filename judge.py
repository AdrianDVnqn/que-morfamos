"""
Juez LLM opcional de calidad de respuesta (no de retrieval), compartido entre
run_benchmark.py y run_langsmith_eval.py. Reutiliza el estilo de prompt del
Juez de candidatos existente en main.py (JSON estricto en español).
"""
import json

JUDGE_PROMPT_TEMPLATE = """
Eres un evaluador de calidad de respuestas de un chatbot de recomendaciones gastronómicas.
Query del usuario: "{query}"

Respuesta generada por el chatbot:
\"\"\"
{reply}
\"\"\"

Restaurantes que el chatbot devolvió como tarjetas: {actual_names}
Restaurantes que se esperaban como resultado correcto (ground truth curado): {expected_names}

TAREA: Evaluá la respuesta generada, no la elección de restaurantes (eso ya se mide aparte).

Responde JSON estricto:
{{
    "relevancia": <entero 1-5, 5 = responde exactamente lo que se pidió>,
    "fidelidad_ok": <bool, true si no inventa datos que no podrían salir de reseñas reales>,
    "alucinacion_detalle": "<string vacío si fidelidad_ok=true, si no explicá qué se inventó>",
    "tono_ok": <bool, true si el tono es coherente y no es robótico/plano>
}}
"""


def judge_answer_quality(llm, query, reply, actual_names, expected_names):
    """Corre el juez de calidad. `llm` es un cliente LangChain (ej. main.llm_smart) con .invoke()."""
    prompt = JUDGE_PROMPT_TEMPLATE.format(
        query=query,
        reply=(reply or "")[:3000],
        actual_names=actual_names,
        expected_names=expected_names,
    )
    try:
        res = llm.invoke(prompt)
        clean = res.content.strip().replace("```json", "").replace("```", "")
        return json.loads(clean)
    except Exception as e:
        return {"error": str(e)}
