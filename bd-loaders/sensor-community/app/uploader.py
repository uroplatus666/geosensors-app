import requests
import json
import pandas as pd
from datetime import datetime, timedelta, timezone
import logging
import os
import uuid
import dateutil.parser

# Настройка логирования
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")

# Глобальные переменные (обновляются в run_upload из конфига)
BASE_URL = "http://90.156.134.128:8080/FROST-Server/v1.1"
HEADERS = {"Content-Type": "application/json"}
DATA_DIR = "data"

created_ids = {
    "Things": [], "Sensors": [], "Datastreams": [],
    "Locations": [], "HistoricalLocations": [],
    "ObservedProperties": [], "FeaturesOfInterest": []
}


# --- Вспомогательные функции ---

def check_existing(endpoint, filter_str):
    try:
        url = f"{BASE_URL}/{endpoint}?$filter={filter_str}"
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            if data.get("value") and len(data["value"]) > 0:
                return data["value"][0]["@iot.id"]
    except (requests.exceptions.RequestException, ValueError) as e:
        logging.error(f"Error checking existing {endpoint} with filter {filter_str}: {e}")
    return None


def get_last_datastream_time(datastream_id):
    """
    Запрашивает у Frost последнее наблюдение для конкретного Datastream.
    Нужен для избежания дубликатов.
    """
    try:
        url = f"{BASE_URL}/Datastreams({datastream_id})/Observations?$top=1&$orderby=phenomenonTime desc"
        resp = requests.get(url)
        if resp.status_code == 200:
            data = resp.json()
            if data.get('value'):
                obs = data['value'][0]
                t_str = obs['phenomenonTime']
                if '/' in t_str:
                    t_str = t_str.split('/')[0]

                dt = dateutil.parser.isoparse(t_str)
                # Приводим к UTC aware, если сервер вернул без зоны
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
    except Exception as e:
        logging.warning(f"Failed to get last time for DS {datastream_id}: {e}")
    return None


def post_entity(endpoint, data, dry_run=False):
    name = data.get("name", "")
    if "name" in data:
        existing_id = check_existing(endpoint, f"name eq '{name}'")
        if existing_id:
            return existing_id

    if dry_run:
        return str(uuid.uuid4())

    try:
        response = requests.post(f"{BASE_URL}/{endpoint}", headers=HEADERS, json=data)
        # 201 Created или 200 OK
        if response.status_code in [200, 201]:
            try:
                # Пытаемся достать ID из заголовка Location
                location = response.headers.get("location")
                if location:
                    id_ = location.split("(")[-1].rstrip(")")
                    created_ids.get(endpoint, []).append(id_)
                    return id_

                # Или из тела ответа
                response_json = response.json()
                id_ = response_json.get("@iot.id")
                if id_:
                    created_ids.get(endpoint, []).append(id_)
                    return id_
            except Exception:
                pass

        logging.error(f"Error creating {endpoint}: {response.text}")
        return None
    except requests.exceptions.RequestException as e:
        logging.error(f"Request failed for {endpoint}: {e}")
        return None


def create_observed_properties(dry_run=False):
    obs_props = [
        {"name": "Относительная влажность воздуха", "description": "Relative humidity in percent",
         "definition": "http://dbpedia.org/page/Humidity"},
        {"name": "Температура воздуха", "description": "Air temperature in Celsius",
         "definition": "http://dbpedia.org/page/Temperature"},
        {"name": "Атмосферное давление", "description": "Atmospheric pressure in Pa",
         "definition": "http://dbpedia.org/page/Atmospheric_pressure"},
        {"name": "PM2.5",
         "description": "Concentration of particulate matter with diameter up to 2.5 micrometers in µg/m³",
         "definition": "http://dbpedia.org/page/Particulates"},
        {"name": "PM10",
         "description": "Concentration of particulate matter with diameter up to 10 micrometers in µg/m³",
         "definition": "http://dbpedia.org/page/Particulates"},
    ]
    obs_prop_ids = {}
    for prop in obs_props:
        id_ = post_entity("ObservedProperties", prop, dry_run)
        if id_:
            obs_prop_ids[prop["name"]] = id_
    return obs_prop_ids


# --- Основная логика обработки ---

def process_group(group, obs_prop_ids, dry_run=False):
    """
    Создает Thing, Sensors, Locations, Datastreams для группы (Инвентарного номера).
    """
    try:
        inv = group['Инвентарный номер изделия'].iloc[0]
        brand = group['Марка'].iloc[0]
        proc_id = group['Номер процессора'].iloc[0]
        type_ = group['Тип'].iloc[0]
    except Exception:
        return None

    # 1. Create Thing
    thing_data = {
        "name": f"Стационарный датчик пыли {inv}",
        "description": "SDS011+BME280",
        "properties": {"brand": str(brand), "processorId": str(proc_id), "type": str(type_)}
    }
    thing_id = post_entity("Things", thing_data, dry_run)
    if not thing_id: return None

    # 2. Find Sensors IDs
    sds_rows = group[group['sensor_type'] == 'SDS011']
    bme_rows = group[group['sensor_type'] == 'BME280']

    sds_sensor_id_val = sds_rows['sensor_id'].iloc[0] if not sds_rows.empty else None
    bme_sensor_id_val = bme_rows['sensor_id'].iloc[0] if not bme_rows.empty else None

    if not sds_sensor_id_val and not bme_sensor_id_val:
        return None

    # 3. Create Sensors entities
    sds_db_id = None
    if sds_sensor_id_val:
        sds_data = {
            "name": f"SDS011_{inv}",
            "description": "PM Sensor",
            "encodingType": "application/pdf",
            "metadata": "https://nova-fitness.com/SDS011.pdf"
        }
        sds_db_id = post_entity("Sensors", sds_data, dry_run)

    bme_db_id = None
    if bme_sensor_id_val:
        bme_data = {
            "name": f"BME280_{inv}",
            "description": "Meteo Sensor",
            "encodingType": "application/pdf",
            "metadata": "https://bosch.com/BME280.pdf"
        }
        bme_db_id = post_entity("Sensors", bme_data, dry_run)

    # 4. Create Locations / HistoricalLocations / FOI
    # (Восстановленная логика из вашего исходного кода)
    unique_locations = group[['address', 'lon', 'lat', 'first_seen']].drop_duplicates().reset_index(drop=True)
    last_foi_id = None

    for _, row in unique_locations.iterrows():
        try:
            lon, lat = float(row['lon']), float(row['lat'])
            address = str(row['address'])
            first_seen_str = str(row['first_seen'])

            # Парсим время появления локации
            try:
                parsed_time = dateutil.parser.isoparse(first_seen_str)
                first_seen_iso = parsed_time.strftime("%Y-%m-%dT%H:%M:%SZ")
            except ValueError:
                continue  # Skip invalid time

            # Create Location
            loc_data = {
                "name": address,
                "description": f"Location for sensor {inv}",
                "encodingType": "application/vnd.geo+json",
                "location": {"type": "Point", "coordinates": [lon, lat]}
            }
            loc_id = post_entity("Locations", loc_data, dry_run)
            if not loc_id: continue

            # Create HistoricalLocation
            # Проверяем существование
            hist_filter = f"time eq '{first_seen_iso}' and Thing/@iot.id eq {thing_id} and Locations/any(l:l/@iot.id eq {loc_id})"
            existing_hist = check_existing("HistoricalLocations", hist_filter)

            if not existing_hist:
                hist_data = {
                    "time": first_seen_iso,
                    "Thing": {"@iot.id": thing_id},
                    "Locations": [{"@iot.id": loc_id}]
                }
                post_entity("HistoricalLocations", hist_data, dry_run)

            # Create FeatureOfInterest (FOI)
            # Это важно для привязки наблюдений к точке
            foi_data = {
                "name": f"FOI_{inv}_{address}",
                "description": f"Feature of interest for sensor {inv} at {address}",
                "encodingType": "application/vnd.geo+json",
                "feature": {"type": "Point", "coordinates": [lon, lat]}
            }
            foi_id = post_entity("FeaturesOfInterest", foi_data, dry_run)
            if foi_id:
                last_foi_id = foi_id  # Запоминаем последний FOI, чтобы использовать при загрузке

        except Exception as e:
            logging.error(f"Error processing location for {inv}: {e}")

    # 5. Create Datastreams
    ds_ids = {}

    def create_ds(name, desc, unit, symb, sens_db_id, prop_name):
        if not sens_db_id: return None
        ds_data = {
            "name": name,
            "description": desc,
            "observationType": "http://www.opengis.net/def/observationType/OGC-OM/2.0/OM_Measurement",
            "unitOfMeasurement": {"name": unit, "symbol": symb, "definition": "http://unknown"},
            "Thing": {"@iot.id": thing_id},
            "Sensor": {"@iot.id": sens_db_id},
            "ObservedProperty": {"@iot.id": obs_prop_ids[prop_name]}
        }
        return post_entity("Datastreams", ds_data, dry_run)

    ds_ids["P1"] = create_ds(f"PM10_{inv}", "PM10", "microgram per cubic meter", "µg/m³", sds_db_id, "PM10")
    ds_ids["P2"] = create_ds(f"PM2.5_{inv}", "PM2.5", "microgram per cubic meter", "µg/m³", sds_db_id, "PM2.5")
    ds_ids["temperature"] = create_ds(f"Temperature_{inv}", "Temp", "Celsius", "°C", bme_db_id, "Температура воздуха")
    ds_ids["humidity"] = create_ds(f"Humidity_{inv}", "Hum", "Percent", "%", bme_db_id,
                                   "Относительная влажность воздуха")
    ds_ids["pressure"] = create_ds(f"Pressure_{inv}", "Press", "Hectopascal", "hPa", bme_db_id, "Атмосферное давление")

    return {
        "thing_id": thing_id,
        "sds_val": sds_sensor_id_val,
        "bme_val": bme_sensor_id_val,
        "ds_ids": ds_ids,
        "foi_id": last_foi_id  # Передаем FOI ID для наблюдений
    }


def upload_observations_safe(sensor_id, datastream_ids, sensor_type, start_date_str, end_date_str, foi_id=None):
    """
    Загружает наблюдения, проверяя дату на сервере для избежания дублей.
    """
    try:
        start = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        end = datetime.strptime(end_date_str, "%Y-%m-%d").date()
    except Exception:
        return

    # 1. ПРОВЕРКА ДАТЫ НА СЕРВЕРЕ (Дедупликация)
    check_key = "P1" if sensor_type == "SDS011" else "temperature"
    target_ds_id = datastream_ids.get(check_key)

    last_server_time = None
    if target_ds_id:
        last_server_time = get_last_datastream_time(target_ds_id)
        if last_server_time:
            logging.info(f"Sensor {sensor_id} ({sensor_type}): Last data on server {last_server_time}")
        else:
            logging.info(f"Sensor {sensor_id} ({sensor_type}): No data on server. Full upload.")

    current = start
    while current <= end:
        date_str = current.strftime("%Y-%m-%d")

        # Оптимизация: пропуск дня целиком, если он старше данных на сервере
        if last_server_time:
            current_dt_end = datetime.combine(current, datetime.max.time()).replace(tzinfo=timezone.utc)
            if current_dt_end < last_server_time:
                current += timedelta(days=1)
                continue

        # Формирование пути к файлу
        subfolder = "SDS011" if sensor_type == "SDS011" else "BME280"
        filename = f"{date_str}_{sensor_type.lower()}_sensor_{sensor_id}.csv"
        csv_path = os.path.join(DATA_DIR, subfolder, str(sensor_id), filename)

        if not os.path.exists(csv_path):
            current += timedelta(days=1)
            continue

        try:
            df = pd.read_csv(csv_path, sep=";", engine='python')
            if df.empty or "timestamp" not in df.columns:
                current += timedelta(days=1)
                continue

            # Парсинг и приведение к UTC
            df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)

            # ФИЛЬТРАЦИЯ СТРОК (Только новые)
            if last_server_time:
                df = df[df['timestamp'] > last_server_time]

            if df.empty:
                current += timedelta(days=1)
                continue

            observations = []
            keys = ["P1", "P2"] if sensor_type == "SDS011" else ["temperature", "humidity", "pressure"]

            for _, row in df.iterrows():
                ts_str = row['timestamp'].isoformat()

                for key in keys:
                    ds_id = datastream_ids.get(key)
                    val = row.get(key)

                    if ds_id and pd.notna(val):
                        obs = {
                            "phenomenonTime": ts_str,
                            "result": float(val),
                            "Datastream": {"@iot.id": ds_id}
                        }
                        # Привязка FeatureOfInterest (координаты)
                        if foi_id:
                            obs["FeatureOfInterest"] = {"@iot.id": foi_id}

                        observations.append(obs)

            # Отправка
            if observations:
                logging.info(f"Uploading {len(observations)} records for {sensor_id} on {date_str}")
                for obs in observations:
                    requests.post(f"{BASE_URL}/Observations", headers=HEADERS, json=obs)

        except Exception as e:
            logging.error(f"Error processing CSV {csv_path}: {e}")

        current += timedelta(days=1)


def run_upload(config):
    logging.info("--- Starting Upload (Safe Mode) ---")
    global BASE_URL, DATA_DIR
    BASE_URL = config['frost_url']
    DATA_DIR = config['data_dir']

    excel_path = os.path.join(DATA_DIR, "all_stats.xlsx")
    if not os.path.exists(excel_path):
        logging.warning("all_stats.xlsx not found.")
        return

    try:
        df = pd.read_excel(excel_path)
    except Exception:
        logging.error("Failed to read all_stats.xlsx")
        return

    obs_prop_ids = create_observed_properties()

    sds_conf = config['sensors'].get('sds', {})
    bme_conf = config['sensors'].get('bme', {})

    if 'Инвентарный номер изделия' in df.columns:
        groups = df.groupby('Инвентарный номер изделия')
        for inv, group in groups:
            logging.info(f"Processing Inventory: {inv}")

            # Создаем структуру на сервере (Things, Sensors...)
            res = process_group(group, obs_prop_ids)
            if not res: continue

            # Загружаем SDS данные
            if res['sds_val'] and str(res['sds_val']) in sds_conf:
                cfg = sds_conf[str(res['sds_val'])]
                upload_observations_safe(
                    res['sds_val'],
                    res['ds_ids'],
                    "SDS011",
                    cfg['start'],
                    cfg['end'],
                    res['foi_id']  # Передаем ID гео-точки
                )

            # Загружаем BME данные
            if res['bme_val'] and str(res['bme_val']) in bme_conf:
                cfg = bme_conf[str(res['bme_val'])]
                upload_observations_safe(
                    res['bme_val'],
                    res['ds_ids'],
                    "BME280",
                    cfg['start'],
                    cfg['end'],
                    res['foi_id']
                )

    logging.info("--- Upload Finished ---")


if __name__ == "__main__":
    # Для локального теста
    pass