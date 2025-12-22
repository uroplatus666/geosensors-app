FROM python:3.10-slim

# 1. Системные библиотеки (для shapely/postgis/psycopg2)
RUN apt-get update && apt-get install -y \
    binutils \
    libproj-dev \
    gdal-bin \
    libgdal-dev \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

ENV CPLUS_INCLUDE_PATH=/usr/include/gdal
ENV C_INCLUDE_PATH=/usr/include/gdal

# 2. Устанавливаем uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# 3. Настройка окружения
ENV UV_PROJECT_ENVIRONMENT="/venv"
ENV UV_COMPILE_BYTECODE=1 
ENV UV_LINK_MODE=copy
ENV PATH="/venv/bin:$PATH"

WORKDIR /app

# 4. Копируем файлы зависимостей
COPY pyproject.toml uv.lock ./

# 5. Устанавливаем зависимости
RUN uv sync --no-install-project --no-dev

# 6. Копируем код
COPY . .

# 7. Запуск скрипта в бесконечном цикле (буферизация отключена)
CMD ["python", "-u", "ingest_frost.py"]