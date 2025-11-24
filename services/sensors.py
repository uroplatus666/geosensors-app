import psycopg2
import psycopg2.extras
from datetime import datetime, timedelta, timezone
from collections import defaultdict
import decimal
import logging
import config

# Глобальное хранилище данных (кэш в памяти)
dashboard_data = {}
logger = logging.getLogger("app.sensors")


def get_sensor_db_connection():
    try:
        conn = psycopg2.connect(
            host=config.SENSOR_DB_HOST,
            port=config.SENSOR_DB_PORT,
            database=config.SENSOR_DB_NAME,
            user=config.SENSOR_DB_USER,
            password=config.SENSOR_DB_PASS
        )
        return conn
    except Exception as e:
        logger.error(f"SENSOR DB ERROR: {e}")
        raise e


def make_safe_key(s: str) -> str:
    safe_chars = "".join(c if c.isalnum() or c in ('_', '-') else '_' for c in (s or "Unknown"))
    return "_".join(filter(None, safe_chars.split('_')))


# --- Вспомогательные функции (Time & Aggregation) ---

def _parse_iso_phen_time(ts):
    if isinstance(ts, datetime): return ts
    if not ts: return None
    s = str(ts).strip()
    # Обработка некоторых форматов ISO/строк
    if '/' in s: s = s.split('/')[-1]
    if s.endswith('Z'): s = s[:-1] + '+00:00'
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _norm_key_10min(ts):
    dt = _parse_iso_phen_time(ts)
    if dt is None: return None, None
    if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
    floored_min = (dt.minute // 10) * 10
    ndt = dt.replace(minute=floored_min, second=0, microsecond=0)
    return ndt.isoformat(), ndt


def _floor_dt_step(dt: datetime, step_minutes: int) -> datetime:
    sec = step_minutes * 60
    t = dt.timestamp()
    floored = int(t // sec) * sec
    return datetime.fromtimestamp(floored, tz=dt.tzinfo or timezone.utc)


def _aggregate_by_step(prop_data, step_minutes: int):
    sums = {};
    counts = {}
    for d in prop_data:
        dt = _parse_iso_phen_time(d.get("timestamp"))
        if dt is None: continue
        if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
        h = _floor_dt_step(dt, step_minutes)
        key = h.isoformat()
        try:
            val = float(d["value"])
            sums[key] = sums.get(key, 0.0) + val
            counts[key] = counts.get(key, 0) + 1
        except (ValueError, TypeError):
            continue

    if not sums: return [], []
    keys_sorted = sorted(sums.keys())
    vals = [sums[k] / counts[k] for k in keys_sorted]
    return keys_sorted, vals


def _parse_range_cutoff(range_str: str):
    if not range_str or range_str.lower() in ("all", "всё", "все"): return None
    now = datetime.now(timezone.utc)
    try:
        s = range_str.strip().lower()
        if s.endswith('d') or s.endswith('д'): return now - timedelta(days=int(s[:-1]))
        if s.endswith('h') or s.endswith('ч'): return now - timedelta(hours=int(s[:-1]))
        if s.endswith('m') or s.endswith('м'): return now - timedelta(days=30 * int(s[:-1]))
    except Exception:
        return None
    return None


# --- Вспомогательные функции (Wind Processing) ---

def pair_wind(dm_list, sm_list):
    dir_by_key = {};
    spd_by_key = {};
    key_dt_map = {}

    for ts, deg in dm_list or []:
        key, ndt = _norm_key_10min(ts)
        if key is None: continue
        dir_by_key[key] = float(deg)
        key_dt_map[("dir", key)] = ndt

    for ts, spd in sm_list or []:
        key, ndt = _norm_key_10min(ts)
        if key is None: continue
        spd_by_key[key] = float(spd)
        key_dt_map[("spd", key)] = ndt

    pairs = []
    # Находим пересечение ключей (временных меток)
    for key in set(dir_by_key.keys()) & set(spd_by_key.keys()):
        dt_norm = key_dt_map.get(("dir", key)) or key_dt_map.get(("spd", key))
        pairs.append((dt_norm, dir_by_key[key], spd_by_key[key]))

    pairs.sort(key=lambda t: t[0], reverse=True)
    return pairs


def build_wind_rose_from_pairs(pairs):
    if not pairs: return {"theta": [], "r": [], "c": []}
    step = 22.5
    bins = [i * step for i in range(16)]

    def sector_center(deg):
        d = deg % 360.0
        idx = int((d + step / 2) // step) % 16
        return bins[idx] + step / 2

    sum_speed = defaultdict(float)
    counts = defaultdict(int)

    for _, deg, spd in pairs:
        center = sector_center(deg)
        counts[center] += 1
        sum_speed[center] += spd

    theta = sorted(counts.keys())
    r = [counts[t] for t in theta]
    c = [round(sum_speed[t] / counts[t], 2) for t in theta]
    return {"theta": theta, "r": r, "c": c}


# --- Основная логика загрузки ---

def load_data_from_db():
    global dashboard_data  # Явно указываем, что пишем в глобальную переменную модуля
    conn = get_sensor_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    print("--- START LOADING SENSOR DATA ---")

    # 1. Загрузка Thing + Location
    cursor.execute("""
        SELECT t.thing_id, t.name as thing_name, l.location_id, l.name as loc_name,
            ST_X(ST_Transform(l.geom, 4326)) as lon, ST_Y(ST_Transform(l.geom, 4326)) as lat
        FROM thing t
        JOIN thing_location tl ON t.thing_id = tl.thing_id
        JOIN location l ON tl.location_id = l.location_id
    """)
    things_raw = cursor.fetchall()

    locations_map = {}
    for row in things_raw:
        loc_id, thing_id = row['location_id'], row['thing_id']
        if loc_id not in locations_map:
            locations_map[loc_id] = {
                "name": row['loc_name'] or "Unknown",
                "lat": row['lat'],
                "lon": row['lon'],
                "things": {}
            }
        locations_map[loc_id]["things"][thing_id] = {
            "id": thing_id,
            "name": row['thing_name'],
            "datastreams": []
        }

    # 2. Загрузка Datastreams + Observed Properties
    cursor.execute("""
        SELECT d.datastream_id, d.thing_id, d.unit_symbol, op.name as prop_name
        FROM datastream d JOIN observed_property op ON d.obs_prop_id = op.obs_prop_id
    """)
    ds_lookup = defaultdict(list)
    for row in cursor.fetchall():
        ds_lookup[row['thing_id']].append(row)

    # 3. Загрузка наблюдений (observations) и формирование структуры
    for loc_id, loc_data in locations_map.items():
        for thing_id, thing_data in loc_data["things"].items():
            datastreams = ds_lookup.get(thing_id, [])
            values = [];
            obs_props_map = {};
            dm_series, sm_series = [], []

            for ds in datastreams:
                prop_orig = ds['prop_name']
                # Маппинг имени свойства в код (например "Температура" -> "Ta")
                prop_code = config.PROP_MAP_DB_TO_CODE.get(prop_orig, prop_orig)

                # Получаем конфиг для свойства (цвет, иконка)
                conf = config.TARGET_PROPS_CONFIG.get(prop_code)

                # Если конфига нет, генерируем дефолтный
                if not conf:
                    default_color = config.COLORS[len(obs_props_map) % len(config.COLORS)]
                    conf = {"desc": prop_orig, "color": default_color, "unit": ds['unit_symbol'] or '',
                            "icon": "activity"}
                else:
                    conf = conf.copy()
                    # Единица измерения из базы приоритетнее, если есть
                    conf['unit'] = ds['unit_symbol'] or conf.get('unit', '')

                obs_props_map[prop_code] = {
                    "name": prop_code,
                    "desc": conf['desc'],
                    "color": conf['color'],
                    "unit": conf['unit'],
                    "icon": conf.get('icon', 'activity')
                }

                # Загружаем последние 2000 записей
                cursor.execute("""
                    SELECT avg_val, hour FROM observation_hour
                    WHERE datastream_id = %s AND location_id = %s
                    ORDER BY hour DESC LIMIT 2000
                """, (ds['datastream_id'], loc_id))

                for obs in cursor.fetchall():
                    val, ts = obs['avg_val'], obs['hour']
                    if val is None: continue
                    if isinstance(val, decimal.Decimal): val = float(val)
                    ts_iso = ts if isinstance(ts, str) else ts.isoformat()

                    values.append({
                        "timestamp": ts_iso,
                        "prop": prop_code,
                        "value": val,
                        "desc": conf['desc'],
                        "unit": conf['unit'],
                        "color": conf['color']
                    })

                    # Собираем серии для ветра отдельно для построения розы ветров
                    if prop_code in ["Dm", "Dn", "Dx"]: dm_series.append((ts_iso, val))
                    if prop_code in ["Sm", "Sn", "Sx"]: sm_series.append((ts_iso, val))

            # Формируем ключ для дашборда и сохраняем данные
            full_key = f"DS__{make_safe_key(loc_data['name'])}__{make_safe_key(thing_data['name'])}"
            target_props = [conf for code, conf in obs_props_map.items() if code in config.CARD_TARGET_CODES]

            thing_data['datastreams'] = obs_props_map

            # Сохраняем в глобальный словарь
            dashboard_data[full_key] = {
                "values": values,
                "obs_props": list(obs_props_map.values()),
                "target_props": target_props,
                "title": f"{thing_data['name']}, {loc_data['name']}",
                "dm_series": dm_series,
                "sm_series": sm_series
            }

            # Доп. данные для маркеров на карте (последние значения)
            thing_data["dashboard_key"] = full_key
            thing_data["latest"] = {}
            for tp in target_props:
                v_list = [v for v in values if v['prop'] == tp['name']]
                if v_list:
                    v_list.sort(key=lambda x: x['timestamp'], reverse=True)
                    thing_data["latest"][tp['name']] = (v_list[0]['value'], v_list[0]['unit'])

    cursor.close()
    conn.close()
    print("--- LOADING COMPLETE ---")

    # Возвращаем карту локаций для отображения маркеров на карте
    return locations_map


# --- Методы доступа (API Helpers) ---

def get_sensor_data(sensor_key):
    """Безопасное получение данных сенсора по ключу."""
    return dashboard_data.get(sensor_key)


def get_all_dashboard_keys():
    """Получение всех ключей дашбордов."""
    return dashboard_data.keys()