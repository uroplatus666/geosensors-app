FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    PIP_NO_CACHE_DIR=1 \
    GUNICORN_WORKERS=2 \
    GUNICORN_THREADS=4 \
    GUNICORN_TIMEOUT=120 \
    REQUEST_TIMEOUT=300 \
    RUDN_BASE_URL=http://94.154.11.74/frost/v1.1 \
    OTHER_BASE_URL=http://90.156.134.128:8080/FROST-Server/v1.1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

# Копируем pyproject.toml и uv.lock и ставим зависимости
COPY pyproject.toml uv.lock .
RUN uv sync --frozen

# Код
COPY hse_geosensors.py ./hse_geosensors.py
COPY output ./output

EXPOSE 8080

# Продакшен-сервер: читаем PORT/GUNICORN_* из окружения (Compose .env тоже подойдёт)
CMD sh -c "/app/.venv/bin/gunicorn \
  -w ${GUNICORN_WORKERS} \
  -k gthread \
  --threads ${GUNICORN_THREADS} \
  -b 0.0.0.0:${PORT} \
  --timeout ${GUNICORN_TIMEOUT} \
  hse_geosensors:app"

