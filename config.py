# config.py
import os
from dotenv import load_dotenv

load_dotenv()

# --- БД СЕНСОРОВ (Frost) ---
SENSOR_DB_HOST = os.getenv("DB_HOST")
SENSOR_DB_PORT = os.getenv("DB_PORT")
SENSOR_DB_NAME = os.getenv("DB_NAME")
SENSOR_DB_USER = os.getenv("DB_USER")
SENSOR_DB_PASS = os.getenv("DB_PASS")

# --- БД GIS (Spatial) ---
GIS_DB_HOST = os.getenv("PGHOST")
GIS_DB_PORT = os.getenv("PGPORT")
GIS_DB_NAME = os.getenv("PGDATABASE")
GIS_DB_USER = os.getenv("PGUSER")
GIS_DB_PASS = os.getenv("PGPASSWORD")

PORT = os.getenv("PORT")

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

# --- Конфигурация растровых слоев ---

# Палитры (цвета от min к max)
COLOR_RAMPS = {
    "elevation": ["#006400", "#f4a460", "#8b4513", "#ffffff"], # Зеленый -> Коричневый -> Белый
    "water": ["#f7fbff", "#deebf7", "#c6dbef", "#9ecae1", "#6baed6", "#4292c6", "#2171b5", "#084594"], # Синие
    "thermal": ["#0000ff", "#00ffff", "#ffff00", "#ff0000", "#8b0000"], # Холодный -> Теплый
    "wind": ["#f0f9e8", "#bae4bc", "#7bccc4", "#43a2ca", "#0868ac"], # Зелено-синие
    "runoff": ["#ffffe5", "#f7fcb9", "#d9f0a3", "#addd8e", "#78c679", "#41ab5d", "#238443", "#005a32"],
    "default": ["#440154", "#3b528b", "#21918c", "#5ec962", "#fde725"] # Viridis style
}

# Метаданные слоев
RASTER_METADATA = {
    ("rasters", "akad_dsm_2024_n36"): {
        "title": "Цифровая модель местности, Апатиты",
        "unit": "м",
        "ramp": "elevation"
    },
    ("rasters", "akad_ortho_2024_n36"): {
        "title": "Ортофотоплан, Апатиты",
        "unit": None,
        "ramp": "rgb" # Специальный маркер для RGB изображений
    },
    ("rasters", "campus_dsm_uav_20200609_n37"): {
        "title": "Цифровая модель местности, РУДН",
        "unit": "м",
        "ramp": "elevation"
    },
    ("rasters", "campus_dtm_uav_20200609_n37"): {
        "title": "Цифровая модель рельефа, РУДН",
        "unit": "м",
        "ramp": "elevation"
    },
    ("rasters", "campus_max_runoff_depth_2m_n37"): {
        "title": "Глубина стока, РУДН",
        "unit": "м",
        "ramp": "water"
    },
    ("rasters", "campus_pet_1m_20240629_14h"): {
        "title": "Потенциальное испарение, РУДН",
        "unit": "мм",
        "ramp": "runoff"
    },
    ("rasters", "campus_temp_1m_20240629_14h"): {
        "title": "Температура воздуха, РУДН",
        "unit": "°C",
        "ramp": "thermal"
    },
    ("rasters", "campus_temp_surface_1m_20240629_14h"): {
        "title": "Температура поверхности, РУДН",
        "unit": "°C",
        "ramp": "thermal"
    },
    ("rasters", "campus_windspeed_ms_1m_20240629_14h"): {
        "title": "Скорость ветра, РУДН",
        "unit": "м/с",
        "ramp": "wind"
    },
}

# --- Конфигурация векторных слоев ---

VECTOR_PRESENTATION = {
    "groups": [
        {
            "name": "Землепользование",
            "table": "public.lulc_campus",
            "column": "landuse",
            "options": [
                {"val": "grass", "label": "Газон", "color": "#D6FEB5"},
                {"val": "buildings", "label": "Здание", "color": "#E0E0E0"},
                {"val": "pedestrian", "label": "Пешеходная зона", "color": "#FDBE68"},
                {"val": "roads", "label": "Дорога", "color": "#67675C"},
                {"val": "parking", "label": "Парковка", "color": "#A17253"},
                {"val": "wasteland", "label": "Открытая почва", "color": "#CDA969"},
                {"val": "tennis", "label": "Теннис", "color": "#C4D8B5"},
                {"val": "stadium", "label": "Стадион", "color": "#F8A312"},
                {"val": "pitch", "label": "Спортплощадка", "color": "#D98701"},
                {"val": "hospital", "label": "Больница", "color": "#F3D6DE"},
                {"val": "smoking zone", "label": "Курилка", "color": "#E41924"},
                {"val": "stone", "label": "Камень", "color": "#282828"},
                {"val": "storage", "label": "Складское помещение", "color": "#CBCBCB"},
            ]
        },
        {
            "name": "Земельный покров",
            "table": "public.lulc_campus",
            "column": "landcover",
            "options": [
                {"val": "lawn", "label": "Газон", "color": "#D6FEB5"},
                {"val": "impervious", "label": "Плитка", "color": "#67675C"},
                {"val": "bare soil", "label": "Открытая почва", "color": "#CDA969"},
            ]
        }
    ],
    "layers": [
        {"table": "public.boundary_campus", "label": "Граница РУДН", "color": "#CBBCD9"},
        {"table": "public.active_tt_campus", "label": "Датчик Tree Talkers", "color": "#91A0A5"},
        {"table": "public.monitoring_points_campus", "label": "Станция мониторинга CO2", "color": "#995B9F"},
        {"table": "public.sampling_campus", "label": "Почвенная проба", "color": "#C8B563"},
        {"table": "public.tree_inventory_campus", "label": "Дерево", "color": "#318345"},
    ]
}
