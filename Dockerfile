FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# 1. INSTALAMOS HERRAMIENTAS DE COMPILACIÓN (Necesario para simsimd)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    g++ \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# 2. ACTUALIZAMOS PIP (Muy importante para encontrar las versiones correctas)
RUN pip install --no-cache-dir --upgrade pip

COPY requirements.txt .

# 3. INSTALAMOS DEPENDENCIAS
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]