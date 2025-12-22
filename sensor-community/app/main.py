import logging
import sys
import os
import json
import time
import datetime
from datetime import timedelta, timezone

# Импорты модулей (они должны лежать рядом)
from scraper import scrape_data
from processor import run_processing
from uploader import run_upload

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)


def load_config():
    # Путь к конфигу внутри контейнера
    config_path = 'config.json'

    # Пытаемся найти конфиг
    if os.path.exists(config_path):
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
    elif os.path.exists('app/config.json'):  # Fallback для локального запуска
        with open('app/config.json', 'r', encoding='utf-8') as f:
            config = json.load(f)
    else:
        config = {}

    # Переопределение из ENV (для Docker)
    env_token = os.getenv('MAPBOX_TOKEN')
    if env_token:
        config['mapbox_token'] = env_token

    env_frost = os.getenv('FROST_URL')
    if env_frost:
        config['frost_url'] = env_frost

    # Путь к данным (в Docker volume это /data)
    if 'data_dir' not in config:
        config['data_dir'] = '/data'

    return config


# --- НОВАЯ ФУНКЦИЯ: Умный парсинг даты ---
def parse_date(date_str):
    """Пытается распарсить дату в разных форматах."""
    if not date_str:
        return None

    formats = [
        "%Y-%m-%d",  # 2025-09-30 (ISO)
        "%d.%m.%Y",  # 30.09.2025 (Russian/German)
        "%Y/%m/%d"  # 2025/09/30
    ]

    for fmt in formats:
        try:
            return datetime.datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue

    raise ValueError(f"Неизвестный формат даты: {date_str}. Ожидается YYYY-MM-DD или DD.MM.YYYY")


# --- РАБОТА СО STATE-ФАЙЛОМ ---

def get_state_file_path(config):
    data_dir = config.get('data_dir', '/data')
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, 'state.json')


def load_state(state_path):
    if not os.path.exists(state_path):
        return {}
    try:
        with open(state_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"Error reading state file: {e}")
        return {}


def save_state(state_path, state):
    try:
        with open(state_path, 'w', encoding='utf-8') as f:
            json.dump(state, f, indent=4, ensure_ascii=False)
    except Exception as e:
        logging.error(f"Error saving state file: {e}")


def prepare_schedule_and_state(config, current_state):
    new_state = current_state.copy()
    at_least_one_task = False

    # Для расчета 'auto' (вчерашний день)
    today = datetime.datetime.now().date()

    for s_type in ['sds', 'bme']:
        if s_type not in config.get('sensors', {}):
            continue
        if s_type not in new_state:
            new_state[s_type] = {}

        for sensor_id, dates in config['sensors'][s_type].items():
            start_cfg = dates.get('start')
            end_cfg = dates.get('end')

            sensor_id_str = str(sensor_id)
            sensor_state = new_state[s_type].get(sensor_id_str, {})
            last_downloaded = sensor_state.get('last_downloaded')

            # --- Логика START ---
            if last_downloaded:
                # Если уже качали, берем следующую дату после последней успешной
                last_date = parse_date(last_downloaded)
                calc_start = last_date + timedelta(days=1)
            else:
                # Если первый раз, берем дату из конфига
                calc_start = parse_date(start_cfg)

            # --- Логика END ---
            if end_cfg == 'auto':
                # 'auto' значит по вчерашний день (архивы появляются с задержкой)
                calc_end = today - timedelta(days=1)
            else:
                calc_end = parse_date(end_cfg)

            # --- Проверка задачи ---
            if calc_start <= calc_end:
                at_least_one_task = True

                # Обновляем конфиг в памяти (переводим в ISO формат для скрапера)
                config['sensors'][s_type][sensor_id]['start'] = str(calc_start)
                config['sensors'][s_type][sensor_id]['end'] = str(calc_end)

                # Подготавливаем стейт (как будто всё скачалось успешно)
                sensor_state['last_downloaded'] = str(calc_end)
                sensor_state['last_run_timestamp'] = datetime.datetime.now().isoformat()
                new_state[s_type][sensor_id_str] = sensor_state
            else:
                # Задача выполнена, пропускаем
                pass

    return config, new_state, at_least_one_task


def job():
    logging.info("🚀 Job started.")
    try:
        config = load_config()
        state_path = get_state_file_path(config)
        current_state = load_state(state_path)

        config, pending_state, has_tasks = prepare_schedule_and_state(config, current_state)

        # 1. SCRAPING
        if has_tasks:
            scrape_data(config)
            # ВАЖНО: Сохраняем стейт, чтобы не качать одно и то же
            save_state(state_path, pending_state)
        else:
            logging.info("💤 Skipping scrape (everything up to date).")

        # 2. PROCESSING
        run_processing(config)

        # 3. UPLOADING
        run_upload(config)

        logging.info("🏁 Job finished successfully.")

    except Exception as e:
        logging.exception(f"🔥 Critical error in main job: {e}")


def main_loop():
    # Загружаем конфиг, чтобы узнать интервал
    config = load_config()
    interval_seconds = config.get('load_interval')
    interval_minutes = interval_seconds / 60

    logging.info(f"Service started. Schedule: every {interval_minutes:.2f} minutes.")

    # Небольшая пауза при старте
    time.sleep(5)

    while True:
        job()
        logging.info(f"Waiting {interval_minutes:.2f} minutes for next run...")
        time.sleep(interval_seconds)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main_loop()