# Используем Python 3.10-slim как базу
FROM python:3.10-slim

# 1. Устанавливаем системные библиотеки (GDAL для карт)
# Это нужно делать ДО установки Python-пакетов
RUN apt-get update && apt-get install -y \
    binutils \
    libproj-dev \
    gdal-bin \
    libgdal-dev \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Настройки для корректной сборки GDAL/Rasterio (если uv решит собирать из исходников)
ENV CPLUS_INCLUDE_PATH=/usr/include/gdal
ENV C_INCLUDE_PATH=/usr/include/gdal

# 2. Устанавливаем uv (копируем официальный бинарник — это best practice)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# Настройки uv для Docker
ENV UV_COMPILE_BYTECODE=1 
ENV UV_LINK_MODE=copy

WORKDIR /app

# 3. Сначала копируем файлы зависимостей (для кэширования слоев Docker)
# Если вы не меняли зависимости, Docker пропустит этот шаг и возьмет кэш
COPY pyproject.toml uv.lock ./

# 4. Синхронизируем зависимости
# --frozen: строго использовать uv.lock (не обновлять версии)
# --no-install-project: пока не ставим само приложение, только библиотеки
RUN uv sync --frozen --no-install-project --no-dev

# 5. Копируем остальной код
COPY . .

# 6. Доустанавливаем проект (если он оформлен как пакет) или просто убеждаемся, что всё ок
RUN uv sync --frozen --no-dev

# Добавляем виртуальное окружение uv в PATH, чтобы команды python/flask работали напрямую
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8080

# 7. Запускаем через uv run или напрямую python (так как PATH уже настроен)
CMD ["uv", "run", "app.py"]