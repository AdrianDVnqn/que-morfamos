import re
import sys
import glob

def parse_log(filename):
    with open(filename, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()

    # Extract Pass Rate
    pass_match = re.search(r"📊 RESUMEN FINAL: (\d+)/(\d+) PASARON", content)
    pass_rate = pass_match.group(0) if pass_match else "N/A"

    # Extract Individual Times
    # Resultado: PASÓ | Mode: resumen | ⏱️ 7.96s
    times = re.findall(r"⏱️ ([\d\.]+)s", content)
    avg_time = sum(map(float, times)) / len(times) if times else 0

    return {
        "file": filename,
        "pass_rate": pass_rate,
        "avg_time": f"{avg_time:.2f}s"
    }

print("## 📊 Comparativa de Benchmarks LLM\n")
print("| Modelo (Log) | Tasa de Éxito | Tiempo Promedio |")
print("|---|---|---|")

logs = glob.glob("bench_*.log")
for log in logs:
    data = parse_log(log)
    name = log.replace("bench_", "").replace(".log", "").upper()
    print(f"| {name} | {data['pass_rate']} | {data['avg_time']} |")
