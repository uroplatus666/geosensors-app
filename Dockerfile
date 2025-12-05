FROM python:3.10-slim

# 1. Системные библиотеки
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

# 3. Настраиваем uv, чтобы venv был НЕ в /app
# Мы создадим его в корне /venv, куда не дотянется docker volume
ENV UV_PROJECT_ENVIRONMENT="/venv"
ENV UV_COMPILE_BYTECODE=1 
ENV UV_LINK_MODE=copy

# Добавляем этот путь в PATH, чтобы python запускался оттуда автоматически
ENV PATH="/venv/bin:$PATH"

WORKDIR /app

# 4. Копируем файлы зависимостей
COPY pyproject.toml uv.lock ./

# 5. Устанавливаем зависимости в /venv
# Убираем --frozen, если вы правили pyproject.toml вручную и не обновляли lock-файл
RUN uv sync --no-install-project --no-dev

# 6. Копируем код
COPY . .

# 7. Финальная синхронизация
RUN uv sync --no-dev

EXPOSE ${PORT:-8080}

# Запускаем через python
CMD ["python", "app.py"]