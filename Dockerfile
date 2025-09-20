FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Немного подстрахуем сборку shapely/pyproj (обычно есть manylinux-колёса, но пусть будет)
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv
# Копируем pyproject.toml и uv.lock
COPY pyproject.toml uv.lock .
RUN uv sync --frozen

# Код
COPY hse_geosensors.py ./hse_geosensors.py
COPY output ./output

EXPOSE 8080

# Продакшен-сервер на 8080
CMD ["/app/.venv/bin/gunicorn","-w","2","-k","gthread","-b","0.0.0.0:8080","--timeout","120","hse_geosensors:app"]
