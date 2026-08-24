import re
import sys
import glob

def parse_log(filename):
    with open(filename, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()

    # Extract Pass Rate
    pass_match = re.search(r"📊 RESUMEN FINAL: (\d+)/(\d+) PASARON", content)
    pass_rate = pass_match.group(0) if pass_match else "N/A"

    # Extract retrieval metrics from the golden dataset summary line
    metrics_match = re.search(
        r"Recall@5=([\d.]+) \| Precision@5=([\d.]+) \| MRR=([\d.]+) \| Intent Accuracy=([\d.]+)",
        content,
    )
    if metrics_match:
        recall, precision, mrr, intent_acc = metrics_match.groups()
    else:
        recall = precision = mrr = intent_acc = "N/A"

    # Extract Individual Times
    # Resultado: PASÓ | Mode: resumen | ⏱️ 7.96s
    times = re.findall(r"⏱️ ([\d\.]+)s", content)
    avg_time = sum(map(float, times)) / len(times) if times else 0

    return {
        "file": filename,
        "pass_rate": pass_rate,
        "avg_time": f"{avg_time:.2f}s",
        "recall": recall,
        "precision": precision,
        "mrr": mrr,
        "intent_acc": intent_acc,
    }

print("## 📊 Comparativa de Benchmarks LLM\n")
print("| Modelo (Log) | Tasa de Éxito | Recall@5 | Precision@5 | MRR | Intent Acc | Tiempo Promedio |")
print("|---|---|---|---|---|---|---|")

logs = glob.glob("bench_*.log")
for log in logs:
    data = parse_log(log)
    name = log.replace("bench_", "").replace(".log", "").upper()
    print(f"| {name} | {data['pass_rate']} | {data['recall']} | {data['precision']} | {data['mrr']} | {data['intent_acc']} | {data['avg_time']} |")
