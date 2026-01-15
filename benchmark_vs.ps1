$env:BENCHMARK_MODULE = "main_experimental"
$env:PYTHONIOENCODING = "utf-8"


Write-Host "🚀 Iniciando Benchmark vs OpenAI..." -ForegroundColor Green
$env:AI_PROVIDER = "openai"
python run_benchmark.py | Tee-Object -FilePath "bench_openai.log"

Write-Host "`n🚀 Iniciando Benchmark vs DeepSeek..." -ForegroundColor Cyan
$env:AI_PROVIDER = "deepseek"
python run_benchmark.py | Tee-Object -FilePath "bench_deepseek.log"

Write-Host "`n✅ Comparación finalizada. Revisa bench_openai.log y bench_deepseek.log"
