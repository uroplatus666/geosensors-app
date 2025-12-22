import os
import time
import math
import random
import requests
import pandas as pd
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, Tuple, Dict, List

# Настройка логирования
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


# ______________________Вспомогательные функции_____________________

def scan_dir(root_path, label):
    logging.info(f'🚀 {label}  ({root_path})')
    if not os.path.isdir(root_path):
        logging.warning('   ❌ директория не найдена')
        return

    # только под-папки 1-го уровня
    for entry in sorted(os.listdir(root_path)):
        sub = os.path.join(root_path, entry)
        if not os.path.isdir(sub):
            continue

        files = [f for f in os.listdir(sub) if os.path.isfile(os.path.join(sub, f))]
        # Подсчет суммарного количества строк во всех файлах
        total_lines = 0
        for f in files:
            file_path = os.path.join(sub, f)
            try:
                with open(file_path, 'r', encoding='utf-8') as file:
                    total_lines += sum(1 for _ in file)
            except (IOError, UnicodeDecodeError):
                logging.warning(f'        ⚠️ Не удалось прочитать файл: {f}')

        logging.info(f'   📁 {entry}/  →  {len(files)} файл(ов), суммарная длина: {total_lines} строк')


def _read_sensor_csv(path):
    last_err = None
    try:
        df = pd.read_csv(path, sep=';')
        sub = df[['timestamp', 'lat', 'lon']].copy()

        # приводим типы (учитываем возможные десятичные запятые)
        sub['lat'] = pd.to_numeric(sub['lat'].astype(str).str.replace(',', '.'), errors='coerce')
        sub['lon'] = pd.to_numeric(sub['lon'].astype(str).str.replace(',', '.'), errors='coerce')
        sub['timestamp'] = pd.to_datetime(sub['timestamp'], errors='coerce', utc=False)

        sub = sub.dropna(subset=['timestamp', 'lat', 'lon'])

        return sub  # успех
    except Exception as e:
        last_err = f'{type(e).__name__}: {e}'

    # если ничего не подошло — даём понять вызывающему коду
    raise RuntimeError(f'Не удалось прочитать {os.path.basename(path)}. Последняя ошибка: {last_err}')


def process_root(root_path, sensor_type):
    """Собирает строки результата для одной корневой папки типа датчика."""
    rows = []
    if not os.path.isdir(root_path):
        return rows

    for entry in sorted(os.listdir(root_path)):
        sub = os.path.join(root_path, entry)
        if not os.path.isdir(sub):
            continue

        sensor_id = entry.strip()
        csv_files = sorted([f for f in os.listdir(sub) if f.lower().endswith('.csv')])
        days_count = len(csv_files)  # по условию = количеству файлов
        if days_count == 0:
            continue

        # границы появления по каждой точной паре (lat, lon)
        loc_bounds = {}  # (lat, lon) -> [min_ts, max_ts]

        for fname in csv_files:
            fpath = os.path.join(sub, fname)
            try:
                df = _read_sensor_csv(fpath)
            except Exception:
                # пропускаем проблемные файлы, чтобы не ронять процесс
                continue

            g = df.groupby(['lat', 'lon'])['timestamp'].agg(['min', 'max']).reset_index()
            for _, r in g.iterrows():
                key = (float(r['lat']), float(r['lon']))
                mn, mx = pd.Timestamp(r['min']), pd.Timestamp(r['max'])
                if key not in loc_bounds:
                    loc_bounds[key] = [mn, mx]
                else:
                    if pd.notna(mn) and (pd.isna(loc_bounds[key][0]) or mn < loc_bounds[key][0]):
                        loc_bounds[key][0] = mn
                    if pd.notna(mx) and (pd.isna(loc_bounds[key][1]) or mx > loc_bounds[key][1]):
                        loc_bounds[key][1] = mx

        # формируем строки результата по всем локациям датчика
        for (lat, lon), (mn, mx) in loc_bounds.items():
            rows.append({
                'sensor_type': sensor_type,
                'sensor_id': sensor_id,
                'days': days_count,
                'lat': lat,
                'lon': lon,
                'first_seen': mn.strftime('%Y-%m-%dT%H:%M:%S') if pd.notna(mn) else None,
                'last_seen': mx.strftime('%Y-%m-%dT%H:%M:%S') if pd.notna(mx) else None,
            })

    return rows


# ____________________Геокодирование_______________________

MAPBOX_ENDPOINT = "https://api.mapbox.com/geocoding/v5/mapbox.places/{lon},{lat}.json"
DEFAULT_THREADS = 8
MAX_RETRIES = 5
TIMEOUT_SEC = 12

FALLBACK_TYPES: List[Optional[str]] = [
    "address,street,neighborhood,locality,place,region,postcode,poi",
    "address,street,place,region,postcode",
    None
]


def _mk_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": "mapbox-revgeo-ru/1.2"})
    return s


def _sleep_backoff(attempt: int, retry_after: Optional[str] = None) -> None:
    if retry_after:
        try:
            time.sleep(min(60.0, float(retry_after)));
            return
        except ValueError:
            pass
    time.sleep(0.5 * (2 ** attempt) + random.uniform(0, 0.25))


def _coerce_float(x):
    try:
        if x is None: return None
        if isinstance(x, (float, int)): return float(x)
        return float(str(x).strip().replace(",", "."))
    except Exception:
        return None


def _looks_swapped(lat: Optional[float], lon: Optional[float]) -> bool:
    if lat is None or lon is None:
        return False
    lat_ok = 40.0 <= lat <= 82.0
    lon_ok = (19.0 <= lon <= 180.0) or (-180.0 <= lon <= -169.0)
    lat_like_lon = (19.0 <= lat <= 180.0) or (-180.0 <= lat <= -169.0)
    lon_like_lat = 40.0 <= lon <= 82.0
    return (not lat_ok and not lon_ok) and (lat_like_lon and lon_like_lat)


def _reverse_once(session: requests.Session, token: str, lon: float, lat: float,
                  *, language: str, country: Optional[str], types: Optional[str]) -> Optional[str]:
    url = MAPBOX_ENDPOINT.format(lon=str(lon), lat=str(lat))
    params = {
        "access_token": token,
        "language": language,
        "limit": 1,
    }
    if country:
        params["country"] = country
    if types:
        params["types"] = types

    for attempt in range(MAX_RETRIES):
        try:
            r = session.get(url, params=params, timeout=TIMEOUT_SEC)
            if r.status_code == 200:
                data = r.json()
                feats = data.get("features") or []
                return feats[0].get("place_name") if feats else None
            if r.status_code in (429, 500, 502, 503, 504):
                _sleep_backoff(attempt, r.headers.get("Retry-After"));
                continue
            if r.status_code in (401, 403):
                raise RuntimeError(f"Mapbox auth error {r.status_code}: {r.text[:200]}")
            if r.status_code in (400, 404, 422):
                return None
            _sleep_backoff(attempt)
        except requests.RequestException:
            _sleep_backoff(attempt)
    return None


def reverse_geocode_point(token: str, lon: float, lat: float,
                          *, language: str = "ru", country: Optional[str] = "ru") -> Optional[str]:
    session = _mk_session()
    for t in FALLBACK_TYPES:
        addr = _reverse_once(session, token, lon, lat, language=language, country=country, types=t)
        if addr: return addr
    for t in FALLBACK_TYPES:
        addr = _reverse_once(session, token, lon, lat, language=language, country=None, types=t)
        if addr: return addr
    for dx, dy in ((1e-4, 0), (-1e-4, 0), (0, 1e-4), (0, -1e-4)):
        for t in FALLBACK_TYPES:
            addr = _reverse_once(session, token, lon + dx, lat + dy, language=language, country=country, types=t)
            if addr: return addr
    return None


def _preflight(token: str) -> None:
    addr = reverse_geocode_point(token, 37.6175, 55.7520, country="ru")
    if not addr:
        raise RuntimeError("Preflight: не получили адрес по тестовой точке. Проверьте токен Mapbox.")


def reverse_geocode_mapbox_bulk(
        df: pd.DataFrame,
        *,
        token: Optional[str] = None,
        lat_col: str = "lat",
        lon_col: str = "lon",
        threads: int = DEFAULT_THREADS,
        language: str = "ru",
        country: Optional[str] = "ru",
        autoswap: bool = True,
        do_preflight: bool = True
) -> pd.Series:
    if not token:
        raise ValueError("Нет токена Mapbox.")

    if lat_col not in df.columns or lon_col not in df.columns:
        raise ValueError(f"В df нет колонок '{lat_col}' и/или '{lon_col}'.")

    lats = df[lat_col].map(_coerce_float)
    lons = df[lon_col].map(_coerce_float)

    if do_preflight:
        _preflight(token)

    coords = []
    for lat, lon in zip(lats, lons):
        if autoswap and _looks_swapped(lat, lon):
            coords.append((lat, lon, True))
        else:
            coords.append((lat, lon, False))

    session = _mk_session()
    cache: Dict[Tuple[float, float], Optional[str]] = {}
    results = [None] * len(df)
    idx_map = list(df.index)

    def _resolve_one(lon: float, lat: float) -> Optional[str]:
        for t in FALLBACK_TYPES:
            a = _reverse_once(session, token, lon, lat, language=language, country=country, types=t)
            if a: return a
        for t in FALLBACK_TYPES:
            a = _reverse_once(session, token, lon, lat, language=language, country=None, types=t)
            if a: return a
        for dx, dy in ((1e-4, 0), (-1e-4, 0), (0, 1e-4), (0, -1e-4)):
            for t in FALLBACK_TYPES:
                a = _reverse_once(session, token, lon + dx, lat + dy, language=language, country=country, types=t)
                if a: return a
        return None

    futures = {}
    with ThreadPoolExecutor(max_workers=max(1, int(threads))) as ex:
        for i, (lat, lon, swapped) in enumerate(coords):
            if lat is None or lon is None or (isinstance(lat, float) and math.isnan(lat)) or (
                    isinstance(lon, float) and math.isnan(lon)):
                results[i] = None;
                continue

            use_lat, use_lon = (lon, lat) if swapped else (lat, lon)
            key = (use_lon, use_lat)
            if key in cache:
                results[i] = cache[key];
                continue
            fut = ex.submit(_resolve_one, use_lon, use_lat)
            futures[fut] = (i, key)

        for fut in as_completed(futures):
            i, key = futures[fut]
            try:
                addr = fut.result()
            except Exception:
                addr = None
            cache[key] = addr
            results[i] = addr

    return pd.Series(results, index=idx_map, name="address_ru")


def norm_id_to_int(s: pd.Series) -> pd.Series:
    out = s.astype(str).str.extract(r'(\d+)')[0]
    out = pd.to_numeric(out, errors='coerce').astype('Int64')
    return out


# ______________________Основная функция________________________

def run_processing(config):
    logging.info("--- Starting Processing ---")
    data_dir = config['data_dir']
    mapbox_token = config.get('mapbox_token')

    if not mapbox_token:
        logging.error("Mapbox token not found in config")
        raise ValueError("Mapbox token missing")

    folder_sds = os.path.join(data_dir, 'SDS011')
    folder_bme = os.path.join(data_dir, 'BME280')
    output_xlsx = os.path.join(data_dir, 'all_stats.xlsx')
    description_path = os.path.join(data_dir, 'description.xlsx')  # Предполагаем, что файл описания тоже в data

    # 1. Статистика
    scan_dir(folder_sds, 'SDS011')
    scan_dir(folder_bme, 'BME280')

    # 2. Сбор данных
    all_rows = []
    all_rows.extend(process_root(folder_sds, 'SDS011'))
    all_rows.extend(process_root(folder_bme, 'BME280'))

    df = pd.DataFrame(all_rows)

    if df.empty:
        logging.warning('⚠️ Итоговая таблица пуста. Проверьте пути и содержимое.')
        return

    df = df.sort_values(['sensor_type', 'sensor_id', 'first_seen', 'lat', 'lon']).reset_index(drop=True)

    # 3. Добавление адреса (Геокодинг)
    logging.info("Starting Reverse Geocoding...")
    df["address"] = reverse_geocode_mapbox_bulk(
        df, token=mapbox_token, lat_col="lat", lon_col="lon", threads=8
    )

    # Сохраняем промежуточный результат (опционально)
    # archive_stats_path = os.path.join(data_dir, 'archive_stats.xlsx')
    # df.to_excel(archive_stats_path, index=False)

    # 4. Добавление характеристик из description.xlsx
    if not os.path.exists(description_path):
        # Если файла в data нет, попробуем поискать в текущей директории приложения (app)
        local_desc = 'description.xlsx'
        if os.path.exists(local_desc):
            description_path = local_desc
        else:
            logging.error(f"Description file not found at {description_path}")
            return

    logging.info(f"Merging with {description_path}...")
    try:
        description = pd.read_excel(description_path)
    except Exception as e:
        logging.error(f"Failed to read description file: {e}")
        return

    d1 = description[['Инвентарный номер изделия', 'Тип', 'Марка',
                      'Номер процессора', 'SDS011']]
    d1 = d1.rename(columns={'SDS011': 'sensor_id'}).dropna()

    d2 = description[['Инвентарный номер изделия', 'Тип', 'Марка',
                      'Номер процессора', 'BME280']]
    d2 = d2.rename(columns={'BME280': 'sensor_id'}).dropna()
    d12 = pd.concat([d1, d2], axis=0)

    df['sensor_id'] = norm_id_to_int(df['sensor_id'])
    d12['sensor_id'] = norm_id_to_int(d12['sensor_id'])

    all_stats = pd.merge(df, d12, how='left', on='sensor_id')
    all_stats.to_excel(output_xlsx, index=False)
    logging.info(f'✅ Готово: {output_xlsx} | строк: {len(all_stats)}')
    logging.info("--- Processing Finished ---")