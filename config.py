# config.py
import os
from dotenv import load_dotenv

load_dotenv()

# Настройки Flask
CACHE_TYPE = "null"

# --- БД СЕНСОРОВ (Frost) ---
SENSOR_DB_HOST = os.getenv("DB_HOST", "db-frost")
SENSOR_DB_PORT = os.getenv("DB_PORT", "5433")
SENSOR_DB_NAME = os.getenv("DB_NAME", "frost")
SENSOR_DB_USER = os.getenv("DB_USER", "frost")
SENSOR_DB_PASS = os.getenv("DB_PASS", "frost")

# --- БД GIS (Spatial) ---
GIS_DB_HOST = os.getenv("PGHOST", "db-spatial")
GIS_DB_PORT = int(os.getenv("PGPORT", "5432"))
GIS_DB_NAME = os.getenv("PGDATABASE", "gis")
GIS_DB_USER = os.getenv("PGUSER", "pguser")
GIS_DB_PASS = os.getenv("PGPASSWORD", "pgpass")

# --- Константы интерфейса ---
COLORS = [
    '#C8A2C8', '#87CEEB', '#5F6A79', '#2F4F4F', '#A0522D', '#4682B4',
    '#556B2F', '#DDA0DD', '#B0C4DE', '#20B2AA', '#A52A2A', '#808080', '#008080'
]
DARK_GREEN = '#2F4F4F'
PALE_BLUE = '#87CEEB'
SLATE = '#5F6A79'

CARD_TARGET_CODES = ["Ta", "Ua", "Pa", "CO2"]

PROP_MAP_DB_TO_CODE = {
    "Температура воздуха": "Ta", "Относительная влажность воздуха": "Ua", "Влажность воздуха": "Ua",
    "Ощущаемая температура воздуха": "Ta", "Атмосферное давление": "Pa", "Концентрация углекислого газа": "CO2",
    "CO2": "CO2", "Pressure": "Pa", "Humidity": "Ua", "Минимальное направление ветра": "Dn",
    "Среднее направление ветра": "Dm", "Максимальное направление ветра": "Dx", "Минимальная скорость ветра": "Sn",
    "Средняя скорость ветра": "Sm", "Максимальная скорость ветра": "Sx", "Осадки": "Rc", "PM2.5": "PM2.5", "PM10": "PM10",
}

TARGET_PROPS_CONFIG = {
    "Ta": {"desc": "Температура воздуха", "color": COLORS[0], "icon": "thermometer-half"},
    "Ua": {"desc": "Относительная влажность воздуха", "color": COLORS[1], "icon": "droplet"},
    "Pa": {"desc": "Атмосферное давление", "color": COLORS[2], "icon": "cloud"},
    "CO2": {"desc": "CO2", "color": COLORS[2], "icon": "cloud-haze2"},
    "Dm": {"desc": "Среднее направление ветра", "color": COLORS[3], "icon": "compass"},
    "Sm": {"desc": "Средняя скорость ветра", "color": COLORS[5], "icon": "wind"},
    "Rc": {"desc": "Осадки", "color": COLORS[9], "icon": "cloud-rain"},
    "PM2.5": {"desc": "PM2.5", "color": COLORS[4], "icon": "virus"},
    "PM10":  {"desc": "PM10",  "color": COLORS[6], "icon": "virus"},
}