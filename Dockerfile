FROM python:3.11-slim

# Переменные для сборки образа
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Установка зависимостей для Shapely/Pyproj
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

# Установка UV
RUN pip install --no-cache-dir uv

# Копирование и установка зависимостей
COPY pyproject.toml uv.lock .
RUN uv sync --frozen

# Копирование кода
COPY hse_geosensors.py ./hse_geosensors.py
COPY output ./output

# Порт по умолчанию, если не указан в .env
EXPOSE ${PORT:-8080}

# Запуск Gunicorn с переменными из окружения (.env подтягивается через Compose или --env-file)
CMD sh -c "/app/.venv/bin/gunicorn \
  -w ${GUNICORN_WORKERS:-2} \
  -k gthread \
  --threads ${GUNICORN_THREADS:-4} \
  -b 0.0.0.0:${PORT:-8080} \
  --timeout ${GUNICORN_TIMEOUT:-120} \
  hse_geosensors:app"
