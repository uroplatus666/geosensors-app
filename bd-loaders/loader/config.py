import os
from datetime import timezone
from dateutil import parser as dtparser

# Интервал работы загрузчика в сек
load_interval = 300
# Читаем переменные окружения, прокинутые через Docker
FROST_URL = os.getenv("FROST_URL", "http://90.156.134.128:8080/FROST-Server/v1.1").rstrip("/")
DSN = os.getenv("PG_DSN", "postgresql://frost:frost@db-frost:5432/frost")

start_from_str = "2024-01-01T00:00:00Z"
START_FROM_DT = dtparser.isoparse(start_from_str).astimezone(timezone.utc)

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Множества для фильтрации (если понадобятся)
DS_INCLUDE = set()
DS_EXCLUDE = set()