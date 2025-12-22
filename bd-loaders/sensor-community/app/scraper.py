import os
import requests
import datetime
import time
import logging


def scrape_data(config):
    logging.info("--- Starting Scraper ---")
    data_dir = config['data_dir']
    base_url = "https://archive.sensor.community/"

    tasks = []
    # Собираем задачи из конфига
    for sensor_id, dates in config['sensors'].get('sds', {}).items():
        tasks.append((sensor_id, 'SDS011', dates['start'], dates['end']))
    for sensor_id, dates in config['sensors'].get('bme', {}).items():
        tasks.append((sensor_id, 'BME280', dates['start'], dates['end']))

    for sensor_id, s_type, start_str, end_str in tasks:
        current = datetime.datetime.strptime(start_str, "%Y-%m-%d").date()
        end = datetime.datetime.strptime(end_str, "%Y-%m-%d").date()

        # Создаем папку
        sensor_dir = os.path.join(data_dir, s_type, sensor_id)
        os.makedirs(sensor_dir, exist_ok=True)

        pattern = f"_{s_type.lower()}_sensor_{sensor_id}.csv"

        while current <= end:
            date_str = str(current)
            full_name = date_str + pattern
            local_path = os.path.join(sensor_dir, full_name)

            # CHECKPOINT: Если файл есть и он больше 0 байт - пропускаем
            if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
                # logging.debug(f"Skipping {full_name}, already exists.")
                current += datetime.timedelta(days=1)
                continue

            url = f"{base_url}{date_str}/{full_name}"
            try:
                # Retry logic
                for attempt in range(3):
                    try:
                        resp = requests.get(url, timeout=10)
                        if resp.status_code == 200:
                            with open(local_path, "w") as f:
                                f.write(resp.text)
                            logging.info(f"Downloaded: {full_name}")
                            break
                        elif resp.status_code == 404:
                            logging.warning(f"Not found: {full_name}")
                            break
                        else:
                            logging.warning(f"Error {resp.status_code} for {full_name}, retrying...")
                            time.sleep(2)
                    except requests.RequestException as e:
                        logging.error(f"Network error: {e}, attempt {attempt + 1}")
                        time.sleep(5)
            except Exception as e:
                logging.error(f"Critical error downloading {full_name}: {e}")

            current += datetime.timedelta(days=1)
    logging.info("--- Scraping Finished ---")