import os
from datetime import timezone
from dateutil import parser as dtparser
from dotenv import load_dotenv

# Интервал работы загрузчика в сек
load_interval = 300

# Загружаем переменные из .env
load_dotenv()

# --- 1. Переменные из .env (Строгая проверка, без дефолтных значений) ---
# Если переменной нет в .env, скрипт упадет с KeyError, как вы и просили.
try:
    FROST_URL = os.environ["FROST_URL"].rstrip("/")
    DSN = os.environ["PG_DSN"]
    LOG_LEVEL = os.environ["LOG_LEVEL"]
    PAGE_TIMEOUT = int(os.environ["PAGE_TIMEOUT"])
    BATCH_SIZE = int(os.environ["BATCH_SIZE"])
except KeyError as e:
    raise RuntimeError(f"Critical configuration error: Missing environment variable {e}") from e

# --- 2. Настройки логики (Config Logic) ---

# Дата начала загрузки
START_FROM = "2024-01-01T00:00:00Z"
START_FROM_DT = dtparser.isoparse(START_FROM).astimezone(timezone.utc)

# Фильтрация локаций
TARGET_LOCATIONS = [
    "Main RUDN University campus"
]

# Маппинг свойств (RUDN)
RUDN_OBS_PROPS = [
    {"code": "Dn", "name": "Минимальное направление ветра", "unit": "°"},
    {"code": "Dm", "name": "Среднее направление ветра", "unit": "°"},
    {"code": "Dx", "name": "Максимальное направление ветра", "unit": "°"},
    {"code": "Sn", "name": "Минимальная скорость ветра", "unit": "м/с"},
    {"code": "Sm", "name": "Средняя скорость ветра", "unit": "м/с"},
    {"code": "Sx", "name": "Максимальная скорость ветра", "unit": "м/с"},
    {"code": "Ta", "name": "Температура воздуха", "unit": "°C"},
    {"code": "Ua", "name": "Относительная влажность воздуха", "unit": "%"},
    {"code": "Pa", "name": "Атмосферное давление", "unit": "hPa"},
    {"code": "Rc", "name": "Осадки", "unit": "мм"},
]