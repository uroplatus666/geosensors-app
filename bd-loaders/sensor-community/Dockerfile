FROM python:3.10-slim

# Отключаем буферизацию логов
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Системные зависимости
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 1. Сначала копируем зависимости из папки app
COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 2. Теперь копируем содержимое папки app внутрь контейнера (.)
COPY app/ .

# Запуск
CMD ["python", "main.py"]