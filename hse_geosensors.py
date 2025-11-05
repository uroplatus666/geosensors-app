
"""Веб-приложение для визуализации данных геосенсоров.

Модуль объединяет:

* конфигурационные блоки, описывающие параметры окружения, визуальную палитру
  и целевые показатели для двух серверов SensorThings;
* набор утилит преобразования координат, временных рядов и агрегирования
  наблюдений, которые используются повторно в нескольких обработчиках;
* Flask-приложение с корневой страницей-картой, API выдачи временных рядов и
  дашбордом, отображающим подготовленные данные.

Файл играет роль единой точки входа, поэтому докстринги подробно объясняют,
какие подготовительные шаги выполняются и как между собой связаны функции,
кэш ``dashboard_data`` и HTTP-маршруты.
"""

import os
import json
import logging
from datetime import datetime, timedelta, timezone
from collections import defaultdict

import folium
from folium.plugins import MarkerCluster
from flask import Flask, render_template_string, request
import requests

from shapely.geometry import shape, Point
from shapely.ops import transform as shp_transform
import pyproj

"""Настройки логирования и глобальные объекты приложения."""
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("vis")

app = Flask(__name__)
app.config["CACHE_TYPE"] = "null"

"""Переменные окружения и таймауты сетевых запросов."""
RUDN_BASE_URL  = os.getenv("RUDN_BASE_URL",  "http://94.154.11.74/frost/v1.1")
OTHER_BASE_URL = os.getenv("OTHER_BASE_URL", "http://90.156.134.128:8080/FROST-Server/v1.1")
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "300"))

"""Цветовые настройки для визуальных компонентов интерфейса."""
colors = [
    '#C8A2C8', '#87CEEB', '#5F6A79', '#2F4F4F', '#A0522D', '#4682B4',
    '#556B2F', '#DDA0DD', '#B0C4DE', '#20B2AA', '#A52A2A', '#808080', '#008080'
]
DARK_GREEN = '#2F4F4F'
PALE_BLUE  = '#87CEEB'
SLATE      = '#5F6A79'

"""Метаданные и целевые показатели для multidatastream сервера РУДН."""
OBS_PROPS = [
    {"name": "Dn", "desc": "Минимальное направление ветра", "unit": "°"},
    {"name": "Dm", "desc": "Среднее направление ветра", "unit": "°"},
    {"name": "Dx", "desc": "Максимальное направление ветра", "unit": "°"},
    {"name": "Sn", "desc": "Минимальная скорость ветра",  "unit": "м/с"},
    {"name": "Sm", "desc": "Средняя скорость ветра",      "unit": "м/с"},
    {"name": "Sx", "desc": "Максимальная скорость ветра", "unit": "м/с"},
    {"name": "Ta", "desc": "Температура воздуха",          "unit": "°C"},
    {"name": "Ua", "desc": "Влажность воздуха",            "unit": "%"},
    {"name": "Pa", "desc": "Атмосферное давление",         "unit": "hPa"},
    {"name": "Rc", "desc": "Осадки",                       "unit": "мм"},
]
INDEX = {p["name"]: i for i, p in enumerate(OBS_PROPS)}
TARGET_PROPS_RUDN = {
    "Ta": {"desc": "Температура воздуха", "color": colors[0], "unit": "°C", "icon": "thermometer-half"},
    "Ua": {"desc": "Влажность воздуха",   "color": colors[1], "unit": "%",  "icon": "droplet"},
    "Pa": {"desc": "Атмосферное давление","color": colors[2], "unit": "hPa","icon": "cloud"},
}

"""Описания целевых датастримов второго сервера SensorThings."""
TARGET_DS_LIST = [
    "Ощущаемая температура воздуха",
    "Температура воздуха",
    "Относительная влажность воздуха",
    "Концентрация углекислого газа",
    "Атмосферное давление"
]

TARGET_PROPS_DS = {
    "Ощущаемая температура воздуха": {"name": "ApparentTemperature", "desc": "Ощущаемая температура воздуха", "color": colors[0], "unit": "°C", "icon": "thermometer-half"},
    "Температура воздуха": {"name": "ApparentTemperature", "desc": "Температура воздуха", "color": colors[0], "unit": "°C", "icon": "thermometer-half"},
    "Относительная влажность воздуха": {"name": "Humidity", "desc": "Относительная влажность воздуха", "color": colors[1], "unit": "%", "icon": "droplet"},
    "Концентрация углекислого газа": {"name": "CO2",                 "desc": "Концентрация углекислого газа","color": colors[2], "unit": "ppm", "icon": "cloud-haze2"},
    "Атмосферное давление":          {"name": "Pressure",            "desc": "Атмосферное давление",          "color": colors[2], "unit": "Pa",   "icon":  "cloud"},
}

"""Кэш подготовленных временных рядов для страниц дашборда."""
dashboard_data = {}

def make_safe_key(s: str) -> str:
    """Возвращает безопасный идентификатор для использования в ключах и DOM.

    Args:
        s: Произвольная строка с названием локации, датастрима или сенсора.

    Returns:
        Строку без пробелов и потенциально конфликтных символов. Пробелы,
        запятые и косые черты заменяются на подчёркивания, а ``None``
        преобразуется в ``"Unknown"``. Такой формат стабильно используется как
        часть ключей ``dashboard_data`` и в HTML-идентификаторах, где запрещены
        пробелы.
    """

    return (s or "Unknown").replace(" ", "_").replace(",", "_").replace("/", "_")

def is_epsg3857(x: float, y: float) -> bool:
    """Эвристически определяет, даны ли координаты в проекции EPSG:3857.

    Args:
        x: Долгота или абсцисса точки.
        y: Широта или ордината точки.

    Returns:
        ``True``, если координаты выходят за допустимые диапазоны WGS84 и,
        вероятно, указаны в метрах (Web Mercator). Это признак того, что точку
        необходимо преобразовать в геодезическую систему координат перед
        отображением на карте.
    """

    return abs(x) > 180 or abs(y) > 90

def parse_location_coords(loc_obj):
    """Извлекает координаты точки из объекта ``Location`` SensorThings.

    Функция служит единым входом для чтения координат, независимо от того,
    представлена ли локация:

    * словарём GeoJSON ``Point`` или ``Feature``;
    * вложенной структурой ``{"value": {...}}`` из ответов FROST;
    * простым словарём с полями ``latitude``/``longitude``.

    Args:
        loc_obj: Объект локации из API. Может быть ``dict`` любого вида или
            ``None``.

    Returns:
        Кортеж ``(lat, lon)`` в системе WGS84. Если координаты отсутствуют или
        формат не распознан, возвращается ``None``. При необходимости точка
        автоматически трансформируется из проекции EPSG:3857.
    """

    if not loc_obj:
        return None
    geo = None
    try:
        if isinstance(loc_obj, dict) and "type" in loc_obj and "coordinates" in loc_obj:
            geo = shape(loc_obj)
        elif isinstance(loc_obj, dict) and loc_obj.get("type") == "Feature" and "geometry" in loc_obj:
            geo = shape(loc_obj["geometry"])
        elif isinstance(loc_obj, dict) and "value" in loc_obj:
            v = loc_obj["value"]
            if isinstance(v, dict) and "type" in v and "coordinates" in v:
                geo = shape(v)
            elif isinstance(v, dict) and v.get("type") == "Feature" and "geometry" in v:
                geo = shape(v["geometry"])
    except Exception as e:
        logger.debug("GeoJSON не распознан: %s", e)

    if geo is not None:
        if geo.geom_type == "Point":
            x, y = geo.x, geo.y
        else:
            c = geo.centroid
            x, y = c.x, c.y
        if is_epsg3857(x, y):
            project = pyproj.Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True).transform
            p = shp_transform(project, Point(x, y))
            lon, lat = p.x, p.y
        else:
            lon, lat = x, y
        return (lat, lon)

    if isinstance(loc_obj, dict):
        lon = loc_obj.get("longitude") or loc_obj.get("lon")
        lat = loc_obj.get("latitude")  or loc_obj.get("lat")
        if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
            return (float(lat), float(lon))
    return None

def _coerce_float_result(res):

    """Преобразует результат наблюдения к ``float`` независимо от формата.

    Args:
        res: Значение поля ``result`` из ответа SensorThings. Может быть числом,
            строкой, словарём или коллекцией.

    Returns:
        Число с плавающей точкой либо ``None``, если числовое значение извлечь
        не удалось. Функция рекурсивно обходит вложенные структуры, проверяя
        распространённые ключи (``value``, ``result``, ``avg`` и т. д.), чтобы
        привести данные к единому формату для последующей агрегации.
    """

    if res is None:
        return None
    if isinstance(res, (int, float)):
        try:
            return float(res)
        except Exception:
            return None
    if isinstance(res, str):
        s = res.strip().replace(',', '.')
        try:
            return float(s)
        except Exception:
            return None
    if isinstance(res, dict):
        for k in ("value", "result", "avg", "mean", "val"):
            if k in res:
                try:
                    return _coerce_float_result(res[k])
                except Exception:
                    pass
        return None
    if isinstance(res, (list, tuple)):
        for item in res:
            v = _coerce_float_result(item)
            if isinstance(v, (int, float)) and v is not None:
                return float(v)
        return None
    return None

def _parse_iso_phen_time(ts: str):

    """Преобразует время наблюдения SensorThings в ``datetime``.

    Args:
        ts: Строка из поля ``phenomenonTime``. Может быть одиночным значением
            либо интервалом ``start/end``.

    Returns:
        Объект ``datetime`` (при необходимости — в UTC) или ``None`` при ошибке.
        Функция корректно обрабатывает суффикс ``Z`` и интервал, беря конечную
        точку диапазона, чтобы дальнейшие вычисления работали с конкретным
        моментом времени.
    """
    if not ts:
        return None
    s = ts.strip()
    if '/' in s:
        s = s.split('/')[-1]
    if s.endswith('Z'):
        s = s[:-1] + '+00:00'
    try:
        return datetime.fromisoformat(s)
    except Exception:
        try:
            base = s.split('+')[0]
        except Exception:
            return None
        try:
            return datetime.fromisoformat(base)
        except Exception:
            return None

def _norm_key_10min(ts: str):
  
    """Нормализует временную метку к 10-минутному интервалу.

    Args:
        ts: Строка с ISO-временем наблюдения.

    Returns:
        Пара ``(iso_key, dt)`` — округлённая строка ISO и ``datetime`` для
        сортировки. Если ``ts`` не распознаётся, возвращает ``(None, None)``.
        Используется ``pair_wind`` при синхронизации скоростей и направлений
        ветра из multidatastream.
    """

    dt = _parse_iso_phen_time(ts)
    if dt is None:
        return None, None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    floored_min = (dt.minute // 10) * 10
    ndt = dt.replace(minute=floored_min, second=0, microsecond=0)
    return ndt.isoformat(), ndt

def _floor_dt_step(dt: datetime, step_minutes: int) -> datetime:

    """Округляет ``datetime`` вниз до ближайшего шага ``step_minutes``.

    Args:
        dt: Исходное время наблюдения.
        step_minutes: Размер шага агрегации в минутах.

    Returns:
        Новый ``datetime`` с сохранённым часовым поясом и нулевыми секундами,
        совпадающий с началом интервала. Используется агрегатором временных
        рядов для стабилизации ключей усреднения.
    """

    sec = step_minutes * 60
    t = dt.timestamp()
    floored = int(t // sec) * sec
    return datetime.fromtimestamp(floored, tz=dt.tzinfo or timezone.utc)

def _aggregate_by_step(prop_data, step_minutes: int):

    """Усредняет временной ряд по заданному шагу.

    Args:
        prop_data: Последовательность словарей ``{"timestamp": ..., "value": ...}``.
        step_minutes: Интервал усреднения. При ``0`` данные возвращаются как есть.

    Returns:
        Кортеж списков ``(timestamps, values)``, отсортированных по возрастанию
        времени. Пустой вход приводит к двум пустым спискам. Значения приводятся
        к ``float``, а метки — к ISO-формату.
    """

    sums = {}
    counts = {}
    for d in prop_data:
        dt = _parse_iso_phen_time(d.get("timestamp"))
        if dt is None:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        h = _floor_dt_step(dt, step_minutes)
        key = h.isoformat()
        sums[key] = sums.get(key, 0.0) + float(d["value"])
        counts[key] = counts.get(key, 0) + 1
    if not sums:
        return [], []
    keys_sorted = sorted(sums.keys())
    vals = [sums[k] / counts[k] for k in keys_sorted]
    return keys_sorted, vals

def _parse_range_cutoff(range_str: str):

    """Определяет минимальную дату диапазона из текстового параметра.

    Args:
        range_str: Строка формата ``"24h"``, ``"7d"`` и т. п. Поддерживаются
            латинские и кириллические суффиксы.

    Returns:
        Объект ``datetime`` в UTC, соответствующий нижней границе диапазона, или
        ``None``, если диапазон не ограничен или произошла ошибка парсинга.
    """

    if not range_str or range_str.lower() in ("all", "всё", "все"):
        return None
    now = datetime.now(timezone.utc)
    try:
        s = range_str.strip().lower()
        if s.endswith('d') or s.endswith('д'):
            days = int(s[:-1])
            return now - timedelta(days=days)
        if s.endswith('h') or s.endswith('ч'):
            hours = int(s[:-1])
            return now - timedelta(hours=hours)
        if s.endswith('m') or s.endswith('м'):
            months = int(s[:-1])
            return now - timedelta(days=30*months)
    except Exception:
        return None
    return None

def get_latest_triplet_from_md(md) -> dict:

    """Читает свежие показатели температуры, влажности и давления.

    Args:
        md: Словарь multidatastream из API FROST с массивом ``Observations``.

    Returns:
        Словарь ``{"Ta": (value, unit), ...}``, где значения приведены к ``float``.
        Используется при формировании всплывающих карточек на карте, чтобы без
        дополнительных запросов показать актуальные данные по ключевым метрикам.
    """

    obs_list = md.get("Observations") or []
    if not obs_list:
        return {}
    latest = obs_list[0]
    result = latest.get("result") or []
    out = {}
    for k in ["Ta", "Ua", "Pa"]:
        idx = INDEX.get(k)
        if idx is not None and idx < len(result) and result[idx] is not None:
            try:
                out[k] = (float(result[idx]), TARGET_PROPS_RUDN[k]["unit"])
            except Exception:
                pass
    return out

def collect_timeseries_from_md(location_name: str, md) -> None:

    """Подготавливает multidatastream для дашборда.

    Алгоритм:

    1. Преобразует каждое наблюдение в стандартный словарь с метаданными о
       показателе, времени и цвете отображения.
    2. Собирает список ``obs_props`` с описанием всех уникальных величин.
    3. Формирует отдельные серии для ``Dm`` и ``Sm`` для будущей розы ветров.
    4. Сохраняет результат в ``dashboard_data`` под ключом ``MD__<локация>__<ID>``.

    Args:
        location_name: Название локации, отображаемое на карте.
        md: Объект multidatastream со встроенными наблюдениями.

    Side Effects:
        Обновляет глобальный словарь ``dashboard_data``.
    """

    md_id = str(md.get('@iot.id'))
    obs_list = md.get("Observations") or []
    if not obs_list or md_id is None:
        return

    values = []
    all_props = []
    names_seen = set()

    dm_series, sm_series = [], []

    for obs in obs_list:
        result = obs.get("result") or []
        ts = obs.get("phenomenonTime")
        limit = min(len(result), len(OBS_PROPS))
        for i in range(limit):
            if result[i] is None:
                continue
            prop = OBS_PROPS[i]
            try:
                val = float(result[i])
            except Exception:
                continue
            values.append({
                "timestamp": ts,
                "prop": prop["name"],
                "value": val,
                "desc": prop["desc"],
                "unit": prop["unit"],
                "color": colors[i % len(colors)]
            })
            if prop["name"] not in names_seen:
                all_props.append({
                    "name": prop["name"],
                    "desc": prop["desc"],
                    "unit": prop["unit"],
                    "color": colors[i % len(colors)]
                })
                names_seen.add(prop["name"])

        idx_dm = INDEX["Dm"]
        idx_sm = INDEX["Sm"]
        if idx_dm < len(result) and result[idx_dm] is not None:
            try:
                dm_series.append((ts, float(result[idx_dm])))
            except Exception:
                pass
        if idx_sm < len(result) and result[idx_sm] is not None:
            try:
                sm_series.append((ts, float(result[idx_sm])))
            except Exception:
                pass

    if values:
        loc_key = make_safe_key(location_name)
        key = f"MD__{loc_key}__{md_id}"
        dashboard_data[key] = {
            "values": values,
            "obs_props": all_props,
            "target_props": [
                {"name": "Ta", "desc": TARGET_PROPS_RUDN["Ta"]["desc"], "icon": TARGET_PROPS_RUDN["Ta"]["icon"],
                 "color": TARGET_PROPS_RUDN["Ta"]["color"], "unit": TARGET_PROPS_RUDN["Ta"]["unit"]},
                {"name": "Ua", "desc": TARGET_PROPS_RUDN["Ua"]["desc"], "icon": TARGET_PROPS_RUDN["Ua"]["icon"],
                 "color": TARGET_PROPS_RUDN["Ua"]["color"], "unit": TARGET_PROPS_RUDN["Ua"]["unit"]},
                {"name": "Pa", "desc": TARGET_PROPS_RUDN["Pa"]["desc"], "icon": TARGET_PROPS_RUDN["Pa"]["icon"],
                 "color": TARGET_PROPS_RUDN["Pa"]["color"], "unit": TARGET_PROPS_RUDN["Pa"]["unit"]},
            ],

            "title": f"{md_id}, {location_name}",
            "dm_series": dm_series,
            "sm_series": sm_series,
            "source": "RUDN"
        }

def get_latest_observation_value_unit(datastream):

    """Возвращает актуальное значение одиночного датастрима.

    Args:
        datastream: Словарь ``Datastream`` из API FROST.

    Returns:
        Кортеж ``(value, unit)``. Значение приводится к ``float`` независимо от
        исходного формата. Если наблюдений нет, возвращается ``(None, "")``.
        Используется при отрисовке мини-плиток во всплывающих окнах карты.
    """


    obs = datastream.get('Observations') or []
    if not obs:
        return None, ""
    latest = obs[0]
    unit = (datastream.get('unitOfMeasurement') or {}).get('symbol', '')
    v = _coerce_float_result(latest.get("result"))
    return (float(v) if v is not None else None), unit

def collect_timeseries_from_thing(location_name: str, thing) -> None:

    """Формирует временные ряды для ``Thing`` с одиночными датастримами.

    Args:
        location_name: Название локации, к которой привязан ``Thing``.
        thing: Словарь ``Thing`` с вложенными ``Datastreams`` и ``Observations``.

    Side Effects:
        Добавляет в ``dashboard_data`` элемент ``DS__<локация>__<Thing>``.

    Notes:
        Известные датастримы получают заранее определённые цвета и иконки, чтобы
        карта и дашборд оставались визуально согласованными. Незнакомые метрики
        не отбрасываются, а получают генеративное описание и тоже попадают в
        интерфейс.
    """

    thing_name = thing.get('name', f"Thing-{thing.get('@iot.id')}")
    datastreams = thing.get('Datastreams') or []
    if not datastreams:
        return

    values, obs_props, targets = [], [], []
    for ds in datastreams:
        obs_prop_info = ds.get('ObservedProperty')
        obs_prop_name = obs_prop_info.get('name', '')
        targets.append(obs_prop_name)
        if obs_prop_name in TARGET_PROPS_DS:
            cfg = TARGET_PROPS_DS[obs_prop_name]
        else:
            cfg = {
                "name": obs_prop_name or f"DS-{ds.get('@iot.id')}",
                "desc": obs_prop_name,
                "color": colors[(3 + len(obs_props)) % len(colors)],
                "unit": (ds.get('unitOfMeasurement') or {}).get('symbol', ''),
                "icon": "activity"
            }
        if not any(p["name"] == cfg["name"] for p in obs_props):
            obs_props.append(cfg)

        for ob in (ds.get('Observations') or []):
            v = _coerce_float_result(ob.get('result'))
            if v is None:
                continue
            values.append({
                "timestamp": ob.get("phenomenonTime"),
                "prop": cfg["name"],
                "value": float(v),
                "desc": cfg["desc"],
                "unit": cfg["unit"],
                "color": cfg["color"]
            })

    if values:
        loc_key = make_safe_key(location_name)
        key = f"DS__{loc_key}__{make_safe_key(thing_name)}"
        target_props_for_cards = [TARGET_PROPS_DS[nm] for nm in targets if nm in TARGET_PROPS_DS]
        dashboard_data[key] = {
            "values": values,
            "obs_props": obs_props,
            "target_props": target_props_for_cards,
            "title": f"{thing_name}, {location_name}",
            "dm_series": [],
            "sm_series": [],
            "source": "OTHER"
        }
        return target_props_for_cards
    return []

def pair_wind(dm_list, sm_list):

    """Совмещает измерения направления и скорости ветра.

    Args:
        dm_list: Последовательность кортежей ``(timestamp, degrees)`` для Dm.
        sm_list: Последовательность кортежей ``(timestamp, speed)`` для Sm.

    Returns:
        Список ``[(dt, deg, spd), ...]`` в порядке убывания времени. Для каждого
        10-минутного окна берётся самая свежая запись, что обеспечивает
        согласованность при построении розы ветров.
    """

    dir_by_key = {}
    spd_by_key = {}
    key_dt_map = {}

    for ts, deg in dm_list or []:
        key, ndt = _norm_key_10min(ts)
        if key is None:
            continue
        if (key not in dir_by_key) or (ndt > key_dt_map.get(("dir", key), datetime.min.replace(tzinfo=timezone.utc))):
            dir_by_key[key] = float(deg)
            key_dt_map[("dir", key)] = ndt

    for ts, spd in sm_list or []:
        key, ndt = _norm_key_10min(ts)
        if key is None:
            continue
        if (key not in spd_by_key) or (ndt > key_dt_map.get(("spd", key), datetime.min.replace(tzinfo=timezone.utc))):
            spd_by_key[key] = float(spd)
            key_dt_map[("spd", key)] = ndt

    pairs = []
    for key in set(dir_by_key.keys()) & set(spd_by_key.keys()):
        dt_norm = max(key_dt_map.get(("dir", key)), key_dt_map.get(("spd", key)))
        pairs.append((dt_norm, dir_by_key[key], spd_by_key[key]))

    pairs.sort(key=lambda t: t[0], reverse=True)
    return pairs

def build_wind_rose_from_pairs(pairs):

    """Готовит агрегированные данные для розы ветров.

    Args:
        pairs: Список ``(dt, deg, spd)``, полученный из ``pair_wind``.

    Returns:
        Словарь с ключами ``theta`` (центры секторов), ``r`` (количество
        наблюдений) и ``c`` (средняя скорость ветра). Пустой вход приводит к
        пустым спискам, что позволяет фронтенду показать заглушку без ошибок.
    """

    if not pairs:
        return {"theta": [], "r": [], "c": []}

    step = 22.5
    bins = [i * step for i in range(16)]
    def sector_center(deg):
        """Возвращает центральный угол для сектора розы ветров."""

        d = deg % 360.0
        idx = int((d + step/2) // step) % 16
        return bins[idx] + step/2

    sum_speed = defaultdict(float)
    counts = defaultdict(int)
    for _, deg, spd in pairs:
        center = sector_center(deg)
        counts[center] += 1
        sum_speed[center] += spd

    theta = sorted(counts.keys())
    r = [counts[t] for t in theta]
    c = [round(sum_speed[t]/counts[t], 2) for t in theta]
    return {"theta": theta, "r": r, "c": c}

@app.route("/")
def root_map():

    """Рендерит корневую карту и наполняет кэш ``dashboard_data``.

    Returns:
        HTML-строку, полученную из ``folium.Map._repr_html_``.

    Workflow:
        1. Конфигурирует стили, шрифты и вспомогательные JS-функции попапов.
        2. Загружает данные сервера РУДН, создаёт маркеры на карте и записывает
           multidatastream-ы в ``dashboard_data``.
        3. Повторяет процедуру для второго сервера, группируя датастримы по
           ``Thing`` и формируя переключатели внутри попапов.

    Side Effects:
        Обновляет глобальное хранилище ``dashboard_data``.
    """

    m = folium.Map(location=(55.7558, 37.6175), zoom_start=12, tiles='CartoDB positron')

    m.get_root().header.add_child(folium.Element("""
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@500;700&family=Poppins:wght@600;700&display=swap" rel="stylesheet">
        <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
        <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
        <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.10.0/font/bootstrap-icons.css">
        <style>
            .sensor-popup h4 { font-family:'Poppins','Inter',sans-serif; font-weight:700; font-size:1.3em; margin-bottom:10px; }
            .radio-block { margin-bottom:8px; }
            .radio-block .form-check-label { font-weight:600; font-size:1.0em; }

            .mini-metrics { display:grid; grid-template-columns:repeat(3,1fr); gap:10px; margin:10px 0; }
            .mini-metric { display:flex; flex-direction:column; align-items:center; gap:8px; padding:12px; border-radius:12px;
                           font-family:'Inter',system-ui; font-weight:600; font-size:1.05em; text-align:center; }
            .mini-ta { background:rgba(200,162,200,.2); }
            .mini-ua { background:rgba(135,206,235,.2); }
            .mini-pa { background:rgba(95,106,121,.2); }

            .mini-apparenttemperature { background:rgba(200,162,200,.2); }
            .mini-humidity            { background:rgba(135,206,235,.2); }
            .mini-co2                 { background:rgba(95,106,121,.2); }
            .mini-pressure            { background:rgba(95,106,121,.2); }

            .mini-icon { font-size:1.6em; }
            .mini-value { font-size:1.15em; font-weight:700; }
            .mini-label { font-size:.85em; opacity:.75; }

            .dashboard-btn { background:#000; color:#fff !important; font-weight:700; padding:10px 18px; border-radius:8px;
                             text-decoration:none; display:inline-block; margin-top:10px; }
        </style>
        <script>
            function switchMD(containerId, mdId) {
                document.querySelectorAll('#' + containerId + ' .md-metrics').forEach(el => el.style.display = 'none');
                const shown = document.getElementById('metrics-' + mdId);
                if (shown) shown.style.display = 'block';
                document.querySelectorAll('#' + containerId + ' .dash-btn').forEach(el => el.style.display='none');
                const btn = document.getElementById('btn-' + mdId);
                if (btn) btn.style.display='inline-block';
            }
            function switchThing(containerId, thingId) {
                document.querySelectorAll('#' + containerId + ' .thing-metrics').forEach(el => el.style.display = 'none');
                const shown = document.getElementById('metrics-thing-' + thingId);
                if (shown) shown.style.display = 'block';
                document.querySelectorAll('#' + containerId + ' .dash-btn').forEach(el => el.style.display='none');
                const btn = document.getElementById('btn-thing-' + thingId);
                if (btn) btn.style.display='inline-block';
            }
        </script>
    """))

    marker_cluster = MarkerCluster().add_to(m)
    icon_url = 'https://cdn-icons-png.flaticon.com/512/10338/10338121.png'

    url_rudn = f"{RUDN_BASE_URL}/Locations?$expand=Things($expand=MultiDatastreams($expand=Observations($orderby=phenomenonTime desc;$top=10000)))"
    try:
        logger.debug("RUDN запрос: %s", url_rudn)
        resp = requests.get(url_rudn, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        logger.exception("Ошибка запроса RUDN")
        data = {"value": []}

    for loc in data.get("value", []):
        location_name = loc.get("name", "Unknown RUDN")
        latlon = parse_location_coords(loc.get("location"))
        if not latlon:
            logger.warning("Нет координат у %s", location_name)
            continue
        lat, lon = latlon

        md_list = []
        for th in (loc.get("Things") or []):
            md_list.extend(th.get("MultiDatastreams") or [])

        container_id = f"MD-{make_safe_key(location_name)}"
        popup_html = [f'<div id="{container_id}" class="sensor-popup"><h4>{location_name}</h4>']

        if not md_list:
            popup_html.append('<p>К этой локации не привязаны MultiDatastreams</p>')
        else:
            popup_html.append('<div class="radio-block">')
            for i, md in enumerate(md_list):
                mdid = str(md.get('@iot.id'))
                checked = 'checked' if i == 0 else ''
                popup_html.append(f"""
                    <div class="form-check">
                        <input class="form-check-input" type="radio" name="md-{container_id}" id="md-{mdid}" {checked}
                               onclick="switchMD('{container_id}', '{mdid}')">
                        <label class="form-check-label" for="md-{mdid}">{mdid}</label>
                    </div>
                """)
            popup_html.append('</div>')

            for i, md in enumerate(md_list):
                mdid = str(md.get('@iot.id'))
                key = f"MD__{make_safe_key(location_name)}__{mdid}"

                collect_timeseries_from_md(location_name, md)
                has_any_obs = bool(md.get("Observations"))
                latest = get_latest_triplet_from_md(md) if has_any_obs else {}
                display = "block" if i == 0 else "none"

                popup_html.append(f'<div id="metrics-{mdid}" class="md-metrics" style="display:{display}">')
                if not has_any_obs:
                    popup_html.append('<p class="text-muted mb-2">Нет данных (Observations) за последние 24 часа</p>')
                else:
                    popup_html.append('<div class="mini-metrics">')
                    for prop in ["Ta", "Ua", "Pa"]:
                        conf = TARGET_PROPS_RUDN[prop]
                        val = latest.get(prop)
                        value_str = f"{round(val[0],1)}{val[1]}" if val else "—"
                        popup_html.append(f"""
                            <div class="mini-metric mini-{prop.lower()}">
                                <div class="mini-icon"><i class="bi bi-{conf['icon']}"></i></div>
                                <div class="mini-value">{value_str}</div>
                                <div class="mini-label">{conf['desc']}</div>
                            </div>
                        """)
                    popup_html.append('</div>')
                if key in dashboard_data and dashboard_data[key]["values"]:
                    popup_html.append(f'<a class="dashboard-btn dash-btn" id="btn-{mdid}" href="/dashboard/{key}">Дашборд</a>')
                popup_html.append('</div>')

        popup_html.append('</div>')
        folium.Marker(
            location=(lat, lon),
            popup=folium.Popup("".join(popup_html), max_width=360, min_width=320),
            tooltip=f"RUDN · {location_name}",
            icon=folium.CustomIcon(icon_url, icon_size=(32, 32), icon_anchor=(16, 32), popup_anchor=(0, -32))
        ).add_to(marker_cluster)

    # Второй сервер
    url_ds = (
        f"{OTHER_BASE_URL}/Locations?"
        "$expand=Things("
        "$expand=Datastreams("
        "$expand=Observations($orderby=phenomenonTime desc;$top=100000),"
        "ObservedProperty"
        ")),"
        "HistoricalLocations($expand=Thing("
        "$expand=Datastreams("
        "$expand=Observations($orderby=phenomenonTime desc;$top=100000),"
        "ObservedProperty"
        ")))"
    )
    try:
        logger.debug("OTHER запрос: %s", url_ds)
        resp2 = requests.get(url_ds, timeout=REQUEST_TIMEOUT)
        resp2.raise_for_status()
        data2 = resp2.json()
    except Exception:
        logger.exception("Ошибка запроса второго сервера")
        data2 = {"value": []}

    for loc in data2.get("value", []):
        location_name = loc.get('name', 'Unknown Location')
        coords = parse_location_coords(loc.get('location'))
        if not coords:
            logger.warning("Нет координат у %s (2й сервер)", location_name)
            continue
        lat, lon = coords

        things = loc.get('Things') or []
        if not things:
            hlocs = loc.get('HistoricalLocations') or []
            things = [hl.get('Thing') for hl in hlocs if hl.get('Thing')]

        container_id = f"DS-{make_safe_key(location_name)}"
        popup_html = [f'<div id="{container_id}" class="sensor-popup"><h4>{location_name}</h4>']
        if not things:
            popup_html.append('<p>К этой локации не привязаны сенсоры</p>')
        else:
            popup_html.append('<div class="radio-block">')
            for i, th in enumerate(things):
                tid = th.get('@iot.id')
                tname = th.get('name', f"Thing-{tid}")
                checked = 'checked' if i == 0 else ''
                popup_html.append(f"""
                    <div class="form-check">
                        <input class="form-check-input" type="radio" name="thing-{container_id}" id="thing-{tid}" {checked}
                               onclick="switchThing('{container_id}', '{tid}')">
                        <label class="form-check-label" for="thing-{tid}">{tname}</label>
                    </div>
                """)
            popup_html.append('</div>')

            for i, th in enumerate(things):
                tid = th.get('@iot.id')
                tname = th.get('name', f"Thing-{tid}")
                datastreams = th.get('Datastreams') or []
                key = f"DS__{make_safe_key(location_name)}__{make_safe_key(tname)}"

                target_props_for_cards = collect_timeseries_from_thing(location_name, th)

                latest_values = {}
                for prop_title in TARGET_DS_LIST:
                    for ds in datastreams:
                        obs_prop_info = ds.get('ObservedProperty')
                        obs_prop_name = obs_prop_info.get('name', '')
                        if obs_prop_name == prop_title:
                            v, u = get_latest_observation_value_unit(ds)
                            latest_values[obs_prop_name] = (v, u)
                            break

                display = "block" if i == 0 else "none"
                popup_html.append(f'<div id="metrics-thing-{tid}" class="thing-metrics" style="display:{display}">')

                if not any(ds.get('Observations') for ds in datastreams):
                    popup_html.append('<p class="text-muted mb-2">Нет данных (Observations)</p>')
                else:
                    popup_html.append('<div class="mini-metrics">')
                    for title in latest_values.keys():
                        cfg = TARGET_PROPS_DS[title]
                        v = latest_values.get(title)
                        s = f"{round(v[0],1)}{v[1]}" if v and v[0] is not None else "—"
                        cls = "mini-" + cfg["name"].lower()
                        popup_html.append(f"""
                            <div class="mini-metric {cls}">
                                <div class="mini-icon"><i class="bi bi-{cfg['icon']}"></i></div>
                                <div class="mini-value">{s}</div>
                                <div class="mini-label">{cfg['desc']}</div>
                            </div>
                        """)
                    popup_html.append('</div>')

                if key in dashboard_data and dashboard_data[key]["values"]:
                    popup_html.append(f'<a class="dashboard-btn dash-btn" id="btn-thing-{tid}" href="/dashboard/{key}">Дашборд</a>')

                popup_html.append('</div>')

        popup_html.append('</div>')
        folium.Marker(
            location=(lat, lon),
            popup=folium.Popup("".join(popup_html), max_width=360, min_width=320),
            tooltip=f"Другой сервер · {location_name}",
            icon=folium.CustomIcon(icon_url, icon_size=(32, 32), icon_anchor=(16, 32), popup_anchor=(0, -32))
        ).add_to(marker_cluster)

    return render_template_string(m._repr_html_())

@app.route("/api/data/<sensor_key>")
def api_data(sensor_key):

    """Отдаёт временные ряды и данные по ветру для выбранного сенсора.

    Query-параметры:
        metrics: Через запятую перечисленные имена показателей.
        range: Временной диапазон (например, ``24h`` или ``7d``).
        agg: Шаг усреднения в минутах (``1h``, ``3h``, ``1d``), ``raw`` — без
            агрегации.

    Returns:
        JSON с полями ``series`` (временные ряды по метрикам) и ``wind`` (данные
        для розы ветров). Функция работает полностью на подготовленном
        ``dashboard_data`` и не делает дополнительных запросов к SensorThings.
    """

    if sensor_key not in dashboard_data:
        return json.dumps([])

    sensor = dashboard_data[sensor_key]
    values = sensor['values']
    obs_props = sensor['obs_props']

    metrics_str = request.args.get('metrics')
    if not metrics_str:
        return json.dumps([])

    try:
        selected = json.loads(metrics_str)
        if not isinstance(selected, list):
            selected = [selected]
    except Exception:
        return json.dumps([])

    range_str = request.args.get('range', '7d')
    agg_str   = request.args.get('agg', '1h')
    cutoff_dt = _parse_range_cutoff(range_str)

    agg_map = {"1h": 60, "3h": 180, "1d": 1440}

    def _filter_by_cutoff(rows):

        """Оставляет только записи, попадающие в заданный диапазон дат."""

        if cutoff_dt is None:
            return rows
        out = []
        for d in rows:
            dt = _parse_iso_phen_time(d.get("timestamp"))
            if dt is None:
                continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt >= cutoff_dt:
                out.append(d)
        return out

    result = []
    for prop_name in selected:
        prop_data_all = [v for v in values if v["prop"] == prop_name]
        if not prop_data_all:
            continue

        prop_data = _filter_by_cutoff(prop_data_all)

        if not prop_data:
            prop_data = sorted(
                prop_data_all,
                key=lambda d: _parse_iso_phen_time(d.get("timestamp")) or datetime.min
            )[-200:]
            if not prop_data:
                continue

        prop_info = next((p for p in obs_props if p["name"] == prop_name), {
            "desc": prop_name, "unit": "", "color": "#999999"
        })
        color = prop_info.get("color", "#999999")

        agg_key = (agg_str or "1h").lower()
        step_minutes = 60 if agg_key in ("auto", "raw") else agg_map.get(agg_key, 60)

        ts_list, val_list = _aggregate_by_step(prop_data, step_minutes)

        if not ts_list and prop_data:
            prop_data_sorted = sorted(
                prop_data,
                key=lambda d: _parse_iso_phen_time(d.get("timestamp")) or datetime.min
            )
            ts_list = [d["timestamp"] for d in prop_data_sorted]
            val_list = [d["value"] for d in prop_data_sorted]

        result.append({
            "prop": prop_name,
            "timestamps": ts_list,
            "values": val_list,
            "desc": prop_info["desc"],
            "color": color,
            "unit": prop_info["unit"]
        })
    return json.dumps(result)

@app.route("/dashboard/<sensor_key>")
def dashboard(sensor_key):

    """Отрисовывает дашборд для выбранного сенсора.

    Args:
        sensor_key: Ключ из ``dashboard_data`` (например, ``MD__...`` или ``DS__...``).

    Returns:
        Пара ``(html, status)``. В случае успеха возвращается HTML-страница с
        графиками Plotly, карточками текущих значений и розой ветров.

    Notes:
        Функция не выполняет сетевых запросов: она полностью опирается на
        подготовленные в ``root_map`` данные. Обработчик также формирует список
        доступных сенсоров для переключения внутри дашборда.
    """

    if sensor_key not in dashboard_data:
        return f"<h3>Нет данных для {sensor_key}</h3>", 404

    sensor = dashboard_data[sensor_key]
    values = sensor.get("values", [])
    obs_props = sensor.get("obs_props", [])
    target_props = sensor.get("target_props", [])
    title = sensor.get("title", sensor_key.replace('_',' '))
    dm_series = sensor.get("dm_series", [])
    sm_series = sensor.get("sm_series", [])

    wind_pairs = pair_wind(dm_series, sm_series)
    has_wind = bool(wind_pairs)

    # текущие карточки: берём последние известные значения целевых параметров (по первым попавшимся точкам)
    current = {}
    for tcfg in target_props:
        print(tcfg['name'])
        v = next((vv for vv in values if vv["prop"] == tcfg['name']), None)

        if v:
            current[tcfg['name']] = {"value": v["value"], "unit": tcfg["unit"], "desc": tcfg["desc"], "icon": tcfg["icon"]}

    dir_str = "—"
    last_dm = None
    last_sm = None
    if has_wind:
        _, last_deg, last_spd = wind_pairs[0]
        last_dm = round(float(last_deg), 1)
        last_sm = round(float(last_spd), 1)
        dirs = ['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW']
        idx = int(((last_dm % 360) + 11.25) // 22.5) % 16
        dir_str = f"{int(round(last_dm))}° ({dirs[idx]})"

    rose = build_wind_rose_from_pairs(wind_pairs) if has_wind else {"theta": [], "r": [], "c": []}

    sensors = [
        {"key": k, "title": dashboard_data[k].get("title", k.replace('_', ' '))}
        for k in dashboard_data.keys()
    ]
    icon_url = 'https://cdn-icons-png.flaticon.com/512/10338/10338121.png'

    template = """
<!DOCTYPE html>
<html>
<head>
    <title>Дашборд - {{ title }}</title>
    <meta charset="utf-8">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.10.0/font/bootstrap-icons.css">
    <style>
        body { background-color: #f8f9fa; color: #212529; }
        .navbar-dark.bg-primary { background-color: {{ DARK_GREEN }} !important; }
        .navbar .container-fluid { padding-top: 6px; padding-bottom: 6px; }
        .navbar-brand { font-size: 0.95rem; cursor: pointer; }
        .sensor-header h2 { font-size: 1.1rem; margin: 0; white-space: nowrap; }
        .sensor-header { display: flex; align-items: center; gap: 10px; margin-left: auto; }
        .sensor-logo { width: 32px; height: 32px; border-radius: 8px; background: white; padding: 4px; }

        .metrics-container { display: flex; gap: 20px; margin: 16px 0 18px 0; flex-wrap: wrap; }
        .metric-card { background: white; border: none; border-radius: 12px; padding: 18px;
                        box-shadow: 0 4px 12px rgba(0,0,0,0.05); flex: 1; min-width: 260px; }
        .metric-icon { font-size: 2rem; margin-bottom: 6px; }
        .metric-value { font-size: 2.0rem; font-weight: 700; }
        .metric-label { opacity: .8; }
        .temp-card { background: rgba(200,162,200,0.08); border-left: 4px solid {{ colors[0] }}; }
        .humidity-card { background: rgba(135,206,235,0.08); border-left: 4px solid {{ colors[1] }}; }
        .pressure-card { background: rgba(95,106,121,0.08); border-left: 4px solid {{ colors[2] }}; }

        .wind-row { display:flex; gap:18px; align-items:stretch; margin-bottom:16px; }
        .wind-widget { flex:1; display:flex; gap:16px; align-items:center; background:white; border-radius:12px; padding:14px; box-shadow:0 4px 12px rgba(0,0,0,.05); }
        .rose-card { flex:1; background:white; border-radius:12px; padding:14px; box-shadow:0 4px 12px rgba(0,0,0,.05); }
        @media (max-width: 992px) { .wind-row { flex-direction: column; } }

        .wind-face { width:160px; height:160px; border-radius:50%;
                      background:radial-gradient(circle at 50% 50%, #fff, #f3f4f6);
                      border:1px solid #e5e7eb; position:relative; }
        .wind-face .tick { position:absolute; width:2px; height:8px; background:#c7c7c7;
                            left:calc(50% - 1px); top:6px; transform-origin:1px 66px; }
        .wind-face .label { position:absolute; font-weight:600; color:#5b6169; font-size:12px; }
        .wind-needle { position:absolute; left:50%; top:50%; width:0; height:0; transform-origin:0 0; }
        .wind-needle svg { transform: translate(-5px, -68px); }

        .graph-section { display:flex; gap:18px; align-items:stretch; }
        .graph-wrap { flex:1; }
        .metrics-sidebar { width:340px; }
        .sidebar-inner { background:white; border-radius:12px; padding:14px; box-shadow:0 4px 12px rgba(0,0,0,.05); position:sticky; top:12px; }
        @media (max-width: 992px) {
            .graph-section { flex-direction: column; }
            .metrics-sidebar { width:auto; }
        }

        .wrap-select { font-size: 0.92rem; line-height: 1.35; }
        .wrap-select option { font-size: 0.92rem; line-height: 1.35; white-space: normal; word-break: break-word; }

        .graph-card { background: white; border-radius: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.05);
                       overflow: hidden; display: flex; flex-direction: column; width: 100%; min-height: 520px; }
        .graph-header { padding: 12px 16px; border-bottom: 1px solid #eee; display:flex; justify-content:space-between; align-items:center; gap:12px; flex-wrap:wrap; }
        .graph-title { font-weight: 600; margin-bottom: 0; }
        .graph-body { padding: 0; flex: 1; }
        #plotly-graph { height: 100% !important; width: 100% !important; }
    </style>
</head>
<body>
    <nav class="navbar navbar-expand-lg navbar-dark bg-primary">
        <div class="container-fluid">
            <a class="navbar-brand" href="/" onclick="if (history.length > 1) { history.back(); return false; } else { return true; }">← Назад к карте сенсоров</a>
            <div class="sensor-header">
                <div class="dropdown me-2">
                    <button class="btn btn-light btn-sm dropdown-toggle" type="button" id="sensorDropdown" data-bs-toggle="dropdown" aria-expanded="false">
                        Выбрать сенсор
                    </button>
                    <ul class="dropdown-menu" aria-labelledby="sensorDropdown">
                        {% for s in sensors %}
                        <li><a class="dropdown-item" href="/dashboard/{{ s.key }}"><img src="{{ icon_url }}" alt="" width="18" height="18" class="me-2">{{ s.title }}</a></li>
                        {% endfor %}
                    </ul>
                </div>
                <img src="{{ icon_url }}" class="sensor-logo" alt="Sensor">
                <h2 class="text-white mb-0">{{ title }}</h2>
            </div>
        </div>
    </nav>

    <div class="container mt-3">
        <div class="metrics-container">
            {% if current.Ta %}<div class="metric-card temp-card">
                <div class="metric-icon"><i class="bi bi-thermometer-half"></i></div>
                <div class="metric-value">{{ (current.Ta.value|round(1)) ~ current.Ta.unit }}</div>
                <div class="metric-label">{{ current.Ta.desc }}</div>
            </div>{% endif %}
            {% if current.Ua %}<div class="metric-card humidity-card">
                <div class="metric-icon"><i class="bi bi-droplet"></i></div>
                <div class="metric-value">{{ (current.Ua.value|round(1)) ~ current.Ua.unit }}</div>
                <div class="metric-label">{{ current.Ua.desc }}</div>
            </div>{% endif %}
            {% if current.Pa %}<div class="metric-card pressure-card">
                <div class="metric-icon"><i class="bi bi-cloud"></i></div>
                <div class="metric-value">{{ (current.Pa.value|round(1)) ~ current.Pa.unit }}</div>
                <div class="metric-label">{{ current.Pa.desc }}</div>
            </div>{% endif %}

            {% if current.ApparentTemperature %}<div class="metric-card temp-card">
                <div class="metric-icon"><i class="bi bi-thermometer-half"></i></div>
                <div class="metric-value">{{ (current.ApparentTemperature.value|round(1)) ~ current.ApparentTemperature.unit }}</div>
                <div class="metric-label">{{ current.ApparentTemperature.desc }}</div>
            </div>{% endif %}
            {% if current.Humidity %}<div class="metric-card humidity-card">
                <div class="metric-icon"><i class="bi bi-droplet"></i></div>
                <div class="metric-value">{{ (current.Humidity.value|round(1)) ~ current.Humidity.unit }}</div>
                <div class="metric-label">{{ current.Humidity.desc }}</div>
            </div>{% endif %}
            {% if current.CO2 %}<div class="metric-card pressure-card">
                <div class="metric-icon"><i class="bi bi-cloud-haze2"></i></div>
                <div class="metric-value">{{ (current.CO2.value|round(1)) ~ current.CO2.unit }}</div>
                <div class="metric-label">{{ current.CO2.desc }}</div>
            </div>{% endif %}
            {% if current.Pressure %}<div class="metric-card pressure-card">
                <div class="metric-icon"><i class="bi bi-cloud"></i></div>
                <div class="metric-value">{{ (current.Pressure.value|round(1)) ~ current.Pressure.unit }}</div>
                <div class="metric-label">{{ current.Pressure.desc }}</div>
            </div>{% endif %}
        </div>

        {% if has_wind %}
        <div class="wind-row">
            <div class="wind-widget">
                <div class="wind-face" id="wind-face">
                    <div class="wind-needle" id="wind-needle"></div>
                </div>
                <div class="wind-info">
                    <h5>Компас ветра</h5>
                    <div class="text-muted"> </div>
                    <div style="font-size:1.6rem; font-weight:700; margin-top:6px;">{{ (last_sm|default(None)) and (last_sm|round(1)) ~ " м/с" or "—" }}</div>
                    <div style="margin-top:6px;">Направление: {{ dir_str }}</div>
                </div>
            </div>

            <div class="rose-card">
                <h5 class="mb-2">Роза ветров</h5>
                <div id="wind-rose" style="height:240px;"></div>
            </div>
        </div>
        {% endif %}

        <div class="graph-section">
            <div class="graph-wrap">
                <div class="graph-card">
                    <div class="graph-header">
                        <h5 class="graph-title">Измерения</h5>
                        <div class="d-flex align-items-center gap-2 flex-wrap">
                            <label class="form-label mb-0 me-1">Глубина:</label>
                            <select id="range-select" class="form-select form-select-sm">
                                <option value="1d">1д</option>
                                <option value="7d" selected>7д</option>
                                <option value="30d">30д</option>
                                <option value="90d">90д</option>
                                <option value="180d">180д</option>
                                <option value="all">Всё</option>
                            </select>
                            <label class="form-label mb-0 ms-2 me-1">Агрегация:</label>
                            <select id="agg-select" class="form-select form-select-sm">
                                <option value="1h" selected>1ч</option>
                                <option value="3h">3ч</option>
                                <option value="1d">1д</option>
                            </select>
                        </div>
                    </div>
                    <div class="graph-body" id="plotly-graph"></div>
                </div>
            </div>
            <aside class="metrics-sidebar">
                <div class="sidebar-inner">
                    <label for="metrics-select" class="form-label">Выберите параметры для отображения:</label>
                    <select class="form-select wrap-select" id="metrics-select" multiple size="12">
                        {% for p in obs_props %}
                        <option value="{{ p.name }}" title="{{ p.desc }}" {% if loop.first %}selected{% endif %}>{{ p.desc }}</option>
                        {% endfor %}
                    </select>
                </div>
            </aside>
        </div>
    </div>

    <script>
        (function(){
            const face = document.getElementById('wind-face');
            if (!face) return;
            for (let a=0; a<360; a+=30){
                const t = document.createElement('div');
                t.className='tick';
                t.style.transform = "rotate(" + a + "deg)";
                face.appendChild(t);
            }
            const labels = [
                ['N','50%','6px','translate(-50%,0)'],
                ['E','calc(100% - 16px)','50%','translate(0,-50%)'],
                ['S','50%','calc(100% - 16px)','translate(-50%,0)'],
                ['W','6px','50%','translate(0,-50%)'],
            ];
            labels.forEach(([txt,left,top,tr])=>{
                const l=document.createElement('div');
                l.className='label'; l.innerText=txt;
                l.style.left=left; l.style.top=top; l.style.transform=tr; face.appendChild(l);
            });
            const needle = document.getElementById('wind-needle');
            const deg = {{ last_dm if last_dm is not none else 'null' }};
            const spd = {{ last_sm if last_sm is not none else 'null' }};
            if (deg !== null){
                const color = spd===null ? '{{ PALE_BLUE }}' : (spd < 3 ? '{{ PALE_BLUE }}' : (spd < 8 ? '{{ SLATE }}' : '{{ DARK_GREEN }}'));
                needle.innerHTML =
                    '<svg width="10" height="140" viewBox="0 0 10 140">' +
                    '<polygon points="5,5 9,68 5,74 1,68" fill="'+color+'" />' +
                    '<rect x="4" y="74" width="2" height="50" fill="'+color+'"></rect>' +
                    '<circle cx="5" cy="74" r="4" fill="#333"></circle>' +
                    '</svg>';
                needle.style.transform = "rotate(" + deg + "deg)";
            }
        })();

        function ensureSelection() {
          const selEl = document.getElementById('metrics-select');
          if (!selEl) return false;
          const selected = Array.from(selEl.selectedOptions || []);
          if (selected.length > 0) return false;
          if (selEl.options.length > 0) { selEl.options[0].selected = true; return true; }
          return false;
        }

        function updateGraph(){
            const changedByEnsure = ensureSelection();

            const selEl = document.getElementById('metrics-select');
            const sel = Array.from(selEl?.selectedOptions || []).map(o => o.value);
            const el  = document.getElementById('plotly-graph');

            el.innerHTML = '<div class="m-3 text-muted">Загрузка…</div>';

            if (!sel.length) {
                el.innerHTML = '<div class="alert alert-warning m-3">Нет данных для отображения</div>';
                return;
            }
            const r = document.getElementById('range-select')?.value || '7d';
            const a = document.getElementById('agg-select')?.value || '1h';

            const params = new URLSearchParams();
            params.append('metrics', JSON.stringify(sel));
            params.append('range', r);
            params.append('agg', a);

            fetch('/api/data/{{ sensor_key }}?'+params.toString())
            .then(r => r.json())
            .then(resp => {
                if (!resp || !resp.length) {
                    if (!changedByEnsure) {
                        const changed = ensureSelection();
                        if (changed) return updateGraph();
                    }
                    el.innerHTML = '<div class="alert alert-warning m-3">Нет данных для отображения</div>';
                    return;
                }
                el.innerHTML = '';

                const traces = resp.map(m => ({
                    x: m.timestamps.map(ts => new Date(ts)),
                    y: m.values,
                    name: m.desc + (m.unit ? ' ('+m.unit+')' : ''),
                    type: 'scatter', mode: 'lines',
                    line: { color: m.color, width: 1.5 }
                }));

                const allVals = resp.flatMap(m => m.values).filter(v => Number.isFinite(v));
                const minY = allVals.length ? Math.min(...allVals) : null;
                const maxY = allVals.length ? Math.max(...allVals) : null;
                const pad = (minY!==null && maxY!==null) ? (maxY - minY) * 0.1 : 0;

                Plotly.newPlot('plotly-graph', traces, {
                    margin: { t: 25, r: 250, b: 100, l: 60 },
                    font: { family: 'Inter', size: 12 },
                    showlegend: true,
                    legend: { x: 1.02, xanchor: 'left', y: 1, bgcolor: 'rgba(255,255,255,0.8)', bordercolor: '#ddd', borderwidth: 1 },
                    plot_bgcolor: '#ffffff', paper_bgcolor: '#ffffff',
                    xaxis: {
                        type: 'date', tickangle: -30, showgrid: true, gridcolor: '#f0f0f0', zeroline: false,
                        rangeslider: { visible: true, bgcolor: '#d3d3d3', bordercolor: '#888', borderwidth: 1, thickness: 0.1 },
                        rangeselector: {
                            buttons: [
                                { count: 1, label: '1д', step: 'day', stepmode: 'backward' },
                                { count: 7, label: '7д', step: 'day', stepmode: 'backward' },
                                { count: 1, label: '1м', step: 'month', stepmode: 'backward' },
                                { count: 6, label: '6м', step: 'month', stepmode: 'backward' },
                                { count: 1, label: '1г', step: 'year', stepmode: 'backward' },
                                { step: 'all', label: 'Всё' }
                            ]
                        }
                    },
                    yaxis: {
                        automargin: true,
                        range: [
                            (Number.isFinite(minY - pad)?(minY-pad):null),
                            (Number.isFinite(maxY + pad)?(maxY+pad):null)
                        ]
                    }
                }, {responsive:true});
            })
            .catch(() => {
                el.innerHTML = '<div class="alert alert-danger m-3">Ошибка загрузки данных</div>';
            });
        }
        document.getElementById('metrics-select')?.addEventListener('change', updateGraph);
        document.getElementById('range-select')?.addEventListener('change', updateGraph);
        document.getElementById('agg-select')?.addEventListener('change', updateGraph);
        window.onload = function(){ updateGraph(); };

        (function(){
            var el = document.getElementById('wind-rose');
            if (!el) return;
            var theta = {{ rose_theta | tojson }};
            var r = {{ rose_r | tojson }};
            var c = {{ rose_c | tojson }};
            if (!theta.length) {
                el.innerHTML = '<div class="alert alert-warning m-3">Недостаточно данных для розы ветров</div>';
                return;
            }
            var trace = {
                type: 'barpolar',
                theta: theta,
                r: r,
                marker: { color: '{{ DARK_GREEN }}', opacity: 0.85, line: { color: '{{ PALE_BLUE }}', width: 1 } },
                hovertemplate: 'Сектор %{theta}°<br>Частота: %{r}<br>Средняя скорость: %{customdata} м/с<extra></extra>',
                customdata: c
            };
            var layout = {
                polar: {
                    angularaxis: { direction: 'clockwise', thetaunit: 'degrees', tick0: 0, dtick: 45, gridcolor: '#e9ecef', linecolor: '#adb5bd' },
                    radialaxis: { gridcolor: '#e9ecef', linecolor: '#adb5bd' },
                    bgcolor: '#ffffff'
                },
                margin: { t: 10, r: 10, b: 10, l: 10 },
                showlegend: false,
                paper_bgcolor: '#ffffff',
                font: { family: 'Inter' }
            };
            Plotly.newPlot('wind-rose', [trace], layout, {responsive:true});
        })();
    </script>
</body>
</html>
"""
    return render_template_string(
        template,
        title=title,
        sensors=sensors,
        icon_url=icon_url,
        current=current,
        has_wind=has_wind,
        last_dm=last_dm,
        last_sm=last_sm,
        dir_str=dir_str,
        rose_theta=rose["theta"],
        rose_r=rose["r"],
        rose_c=rose["c"],
        obs_props=obs_props,
        sensor_key=sensor_key,
        DARK_GREEN=DARK_GREEN,
        PALE_BLUE=PALE_BLUE,
        SLATE=SLATE,
        colors=colors
    )

@app.get("/healthz")
def healthz():

    """Возвращает технический статус приложения.

    Returns:
        Короткий JSON ``{"ok": True}``, который читают балансировщики и
        оркестраторы контейнеров. Эндпоинт не обращается к внешним сервисам и
        поэтому служит индикатором жизнеспособности самого процесса Flask.
    """

    return {"ok": True}

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", debug=False, port=port)
