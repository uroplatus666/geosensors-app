import folium
from folium.plugins import MarkerCluster
from flask import Flask, render_template_string, request
import requests
import json
import logging
from datetime import datetime
# Настройка логирования
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)
app = Flask(__name__)
app.config["CACHE_TYPE"] = "null"
# Глобальное хранилище данных для дашбордов
dashboard_data = {}
# Палитра цветов
colors = ['#C8A2C8', '#87CEEB', '#5F6A79', '#2F4F4F', '#A0522D', '#4682B4',
          '#556B2F', '#DDA0DD', '#B0C4DE', '#20B2AA', '#A52A2A', '#808080', '#008080']
def get_latest_observation(datastream):
    """Получить последнее наблюдение для Datastream"""
    observations = datastream.get('Observations', [])
    if not observations:
        logger.warning(f"Нет наблюдений для Datastream: {datastream.get('name', 'Unknown')}")
        return None, None
    latest_obs = observations[0] # Observations отсортированы по phenomenonTime desc
    result = latest_obs.get('result')
    if result is None:
        logger.warning(f"Пустое значение result в наблюдении для Datastream: {datastream.get('name', 'Unknown')}")
        return None, None
    return float(result), datastream.get('unitOfMeasurement', {}).get('symbol', '')
def get_sensor_data(thing, location_name):
    """Получить данные для Thing"""
    thing_id = thing.get('@iot.id')
    thing_name = thing.get('name', 'Unknown Thing')
    datastreams = thing.get('Datastreams', [])
   
    logger.debug(f"Обработка Thing: {thing_name} (ID: {thing_id}) в локации: {location_name}")
   
    values = []
    target_props = [
        "Ощущаемая температура воздуха",
        "Влажность воздуха",
        "Концентрация CO2"
    ]
   
    prop_configs = {
        "Ощущаемая температура воздуха": {
            "name": "ApparentTemperature",
            "desc": "Ощущаемая температура воздуха",
            "color": colors[0], # #C8A2C8
            "unit": "°C",
            "icon": "thermometer-half"
        },
        "Влажность воздуха": {
            "name": "Humidity",
            "desc": "Влажность воздуха",
            "color": colors[1], # #87CEEB
            "unit": "%",
            "icon": "droplet"
        },
        "Концентрация CO2": {
            "name": "CO2",
            "desc": "Концентрация CO2",
            "color": colors[2], # #5F6A79
            "unit": "ppm",
            "icon": "cloud-haze2"
        }
    }
   
    # Список всех свойств для графика
    all_props = []
    color_index = 3 # Начинаем с colors[3] для дополнительных Datastreams
    for datastream in datastreams:
        datastream_name = datastream.get('name', '')
        if not datastream_name:
            logger.debug(f"Пропуск Datastream с пустым именем")
            continue
        # Создаем конфигурацию для всех Datastreams
        prop_config = prop_configs.get(datastream_name, {
            "name": datastream_name,
            "desc": datastream_name,
            "color": colors[color_index % len(colors)],
            "unit": datastream.get('unitOfMeasurement', {}).get('symbol', ''),
            "icon": "question-circle"
        })
        all_props.append(prop_config)
        if datastream_name not in target_props:
            color_index += 1 # Увеличиваем индекс цвета для следующего Datastream
       
        observations = datastream.get('Observations', [])
        if not observations:
            logger.warning(f"Нет наблюдений для Datastream: {datastream_name}")
            continue
        for obs in observations:
            result = obs.get('result')
            if result is None or result == 0:
                logger.warning(f"Пропуск наблюдения с result={result} для Datastream: {datastream_name}")
                continue
            values.append({
                "timestamp": obs["phenomenonTime"],
                "prop": prop_config["name"],
                "prop_desc": prop_config["desc"],
                "value": float(result),
                "color": prop_config["color"],
                "unit": prop_config["unit"],
                "icon": prop_config["icon"]
            })
   
    # Сохраняем данные для дашборда
    if values:
        key = f"{location_name.replace(' ', '_')},_{thing_name.replace(' ', '_')}"
        logger.debug(f"Сохранение данных для ключа: {key}")
        dashboard_data[key] = {
            'values': values,
            'obs_props': all_props,
            'target_props': [prop_configs[prop] for prop in target_props if prop in prop_configs] # Только целевые для карточек
        }
    else:
        logger.warning(f"Нет валидных данных для Thing: {thing_name} в локации: {location_name}")
   
    # Возвращаем только значения для целевых свойств
    target_values = [v for v in values if v["prop"] in [prop_configs[prop]["name"] for prop in target_props if prop in prop_configs]]
    return target_values
@app.route("/")
def generate_root_page():
    # Запрос данных
    url = "http://90.156.134.128:8080/FROST-Server/v1.1/Locations?$expand=Things($expand=Datastreams($expand=Observations($orderby=phenomenonTime desc)))"
    try:
        logger.debug(f"Отправка запроса к API: {url}")
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        logger.debug(f"Полученные данные: {json.dumps(data, indent=2)}")
    except requests.RequestException as e:
        logger.error(f"Ошибка при запросе данных: {e}")
        return f"Ошибка при запросе данных: {e}"
   
    # Создаем карту
    m = folium.Map(
        location=(55.7558, 37.6175),
        zoom_start=14,
        tiles='CartoDB positron',
        min_zoom=10,
        max_zoom=19,
        max_bounds=True,
        max_bounds_viscosity=1.0
    )
   
    # Подключаем стили
    m.get_root().header.add_child(folium.Element("""
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@500;700&family=Poppins:wght@600;700&display=swap" rel="stylesheet">
        <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
        <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
        <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.10.0/font/bootstrap-icons.css">
        <style>
            .sensor-popup h4 {
                font-family: 'Poppins', 'Inter', sans-serif;
                font-weight: 700;
                font-size: 1.6em;
                margin-bottom: 15px;
            }
            .mini-metrics {
                display: grid;
                grid-template-columns: repeat(3, 1fr);
                gap: 10px;
                margin: 15px 0;
            }

            .mini-metric {
                display: flex;
                flex-direction: column;
                align-items: center;
                gap: 8px;
                padding: 12px;
                border-radius: 12px;
                font-family: 'Inter', system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
                font-weight: 600;
                font-size: 1.2em;
                text-align: center;
                transition: transform 0.2s;
            }
            .mini-metric:hover {
                transform: translateY(-3px);
                background: rgba(0,0,0,0.08);
            }
            .mini-apparenttemperature {
                background: rgba(200, 162, 200, 0.2);
            }
            .mini-apparenttemperature:hover {
                background: rgba(200, 162, 200, 0.3);
            }
            .mini-humidity {
                background: rgba(135, 206, 235, 0.2);
            }
            .mini-humidity:hover {
                background: rgba(135, 206, 235, 0.3);
            }
            .mini-co2 {
                background: rgba(95, 106, 121, 0.2);
            }
            .mini-co2:hover {
                background: rgba(95, 106, 121, 0.3);
            }

            .mini-apparenttemperature .mini-icon { color: colors[0]; }
            .mini-humidity .mini-icon { color: colors[1]; }
            .mini-co2 .mini-icon { color: colors[2]; }

            .mini-icon {
                font-size: 2.2em;
                margin-bottom: 5px;
            }
            .mini-value { font-size: 1.3em; font-weight: 700; }
            .mini-label { font-size: 0.85em; opacity: 0.7; }

            .dashboard-btn {
                background-color: #000 !important;
                color: white !important;
                font-size: 1.1em !important;
                font-weight: bold !important;
                padding: 12px 24px !important;
                border-radius: 8px !important;
                text-decoration: none !important;
                display: inline-block !important;
                margin-top: 15px !important;
                transition: background-color 0.3s;
            }
            .dashboard-btn:hover {
                background-color: #333 !important;
                color: white !important;
            }
            .cluster-icon {
                background-color: #008B8B;
                color: white;
                border-radius: 50%;
                width: 30px;
                height: 30px;
                display: flex;
                align-items: center;
                justify-content: center;
                font-weight: bold;
                font-size: 14px;
            }
            .thing-radio { margin-bottom: 10px; }
            .thing-radio .form-check-label {
                font-weight: bold;
                font-size: 1.1em;
            }
        </style>
        <script>
            function updateMetrics(thingId) {
                document.querySelectorAll('.thing-metrics').forEach(el => el.style.display = 'none');
                document.getElementById('metrics-' + thingId).style.display = 'block';
                document.querySelectorAll('.dashboard-btn').forEach(btn => btn.style.display = 'none');
                document.getElementById('btn-' + thingId).style.display = 'inline-block';
            }
        </script>
    """))
   
    marker_cluster = MarkerCluster(
        options={
            'iconCreateFunction': '''
                function(cluster) {
                    return L.divIcon({
                        html: '<div class="cluster-icon">' + cluster.getChildCount() + '</div>',
                        className: 'marker-cluster',
                        iconSize: L.point(30, 30)
                    });
                }
            '''
        }
    ).add_to(m)
   
    # Обработка локаций
    for location in data.get('value', []):
        location_name = location.get('name', 'Unknown Location')
        coordinates = location.get('location', {}).get('coordinates')
        if not coordinates or len(coordinates) < 2:
            logger.warning(f"Нет координат для Location: {location_name}")
            continue
        things = location.get('Things', [])
        popup_content = f'<div class="sensor-popup"><h4>{location_name}</h4>'
        if not things:
            popup_content += '<p>К этой локации не привязаны датчики</p>'
        else:
            popup_content += '<div class="thing-radio">'
            for i, thing in enumerate(things):
                thing_id = thing.get('@iot.id')
                thing_name = thing.get('name', 'Unknown Thing')
                checked = 'checked' if i == 0 else ''
                popup_content += f'''
                    <div class="form-check">
                        <input class="form-check-input" type="radio" name="thing" id="thing-{thing_id}" {checked} onclick="updateMetrics('{thing_id}')">
                        <label class="form-check-label" for="thing-{thing_id}">{thing_name}</label>
                    </div>
                '''
            popup_content += '</div>'
            for i, thing in enumerate(things):
                thing_id = thing.get('@iot.id')
                thing_name = thing.get('name', 'Unknown Thing')
                datastreams = thing.get('Datastreams', [])
               
                # Получаем данные для дашборда
                values = get_sensor_data(thing, location_name)
               
                # Формируем безопасный ключ для URL
                safe_key = f"{location_name.replace(' ', '_')},_{thing_name.replace(' ', '_')}"
               
                # Проверяем наличие нужных Datastreams и Observations для попапа
                target_props = ["Ощущаемая температура воздуха", "Влажность воздуха", "Концентрация CO2"]
                has_target_datastreams = any(ds.get('name') in target_props for ds in datastreams)
                has_target_observations = any(ds.get('Observations', []) for ds in datastreams if ds.get('name') in target_props)
                has_any_observations = any(ds.get('Observations', []) for ds in datastreams)
               
                popup_content += f'<div id="metrics-{thing_id}" class="thing-metrics" style="display: {"block" if i == 0 else "none"}">'
                if not has_target_datastreams or not has_target_observations:
                    popup_content += '<p>Отсутствуют данные об ощущаемой температуре, влажности воздуха и концентрации CO2</p>'
                else:
                    popup_content += '<div class="mini-metrics">'
                    for prop in target_props:
                        prop_config = {
                            "Ощущаемая температура воздуха": {"name": "ApparentTemperature", "icon": "thermometer-half", "color": colors[0]},
                            "Влажность воздуха": {"name": "Humidity", "icon": "droplet", "color": colors[1]},
                            "Концентрация CO2": {"name": "CO2", "icon": "cloud-haze2", "color": colors[2]}
                        }.get(prop)
                        value, unit = None, ""
                        for ds in datastreams:
                            if ds.get('name') == prop:
                                value, unit = get_latest_observation(ds)
                                break
                        popup_content += f'''
                            <div class="mini-metric mini-{prop_config["name"].lower()}">
                                <div class="mini-icon"><i class="bi bi-{prop_config["icon"]}"></i></div>
                                <div class="mini-value">{round(value, 1) if value is not None else ''}{unit if value is not None else ''}</div>
                                <div class="mini-label">{prop}</div>
                            </div>
                        '''
                    popup_content += '</div>'
               
                # Кнопка дашборда активна, если есть любые наблюдения
                popup_content += f'<a href="/dashboard/{safe_key}" class="dashboard-btn" id="btn-{thing_id}" style="display: {"inline-block" if i == 0 else "none"}" {"disabled" if not has_any_observations else ""}>Дашборд для {thing_name}</a>'
                popup_content += '</div>'
        popup_content += '</div>'
        popup = folium.Popup(popup_content, max_width=340, min_width=300)
        sensor_icon = folium.CustomIcon(
            icon_image='https://cdn-icons-png.flaticon.com/512/10338/10338121.png',
            icon_size=(32, 32),
            icon_anchor=(16, 32),
            popup_anchor=(0, -32)
        )
        marker = folium.Marker(
            location=(coordinates[1], coordinates[0]),
            popup=popup,
            tooltip=location_name,
            icon=sensor_icon
        )
        marker.add_to(marker_cluster)
   
    m.save("output/index.htm")
    return render_template_string(m._repr_html_())
@app.route("/api/data/<sensor_name>")
def get_api_data(sensor_name):
    """API-эндпоинт для получения данных графика"""
    logger.debug(f"Запрос данных для сенсора: {sensor_name}")
    if sensor_name not in dashboard_data:
        logger.error(f"Данные для сенсора '{sensor_name}' не найдены в dashboard_data")
        return json.dumps([])
   
    sensor_data = dashboard_data[sensor_name]
    values = sensor_data['values']
    obs_props = sensor_data['obs_props']
   
    metrics_str = request.args.get('metrics')
    if not metrics_str:
        logger.warning("Параметр 'metrics' не предоставлен")
        return json.dumps([])
   
    try:
        selected_props = json.loads(metrics_str)
        if not isinstance(selected_props, list):
            selected_props = [selected_props]
    except json.JSONDecodeError as e:
        logger.error(f"Ошибка декодирования JSON в параметре metrics: {e}")
        return json.dumps([])
   
    result = []
    for prop_name in selected_props:
        prop_data = [{"timestamp": v["timestamp"], "value": v["value"]} for v in values if v["prop"] == prop_name]
        if prop_data:
            prop_info = next((p for p in obs_props if p["name"] == prop_name), {
                "desc": prop_name,
                "color": "#999999",
                "unit": ""
            })
            result.append({
                "prop": prop_name,
                "timestamps": [d["timestamp"] for d in prop_data],
                "values": [d["value"] for d in prop_data],
                "desc": prop_info["desc"],
                "color": prop_info["color"],
                "unit": prop_info["unit"]
            })
    logger.debug(f"Возвращаемые данные для сенсора {sensor_name}: {json.dumps(result, indent=2)}")
    return json.dumps(result)
@app.route("/dashboard/<sensor_name>")
def dashboard(sensor_name):
    """Страница дашборда для конкретного сенсора"""
    logger.debug(f"Открытие дашборда для сенсора: {sensor_name}")
    if sensor_name not in dashboard_data:
        logger.error(f"Данные для сенсора '{sensor_name}' не найдены в dashboard_data")
        return f"<h1>Данные для сенсора '{sensor_name.replace('_', ' ')}' не найдены</h1>", 404
   
    sensor_data = dashboard_data[sensor_name]
    values = sensor_data['values']
    obs_props = sensor_data['obs_props']
    target_props = sensor_data['target_props']
   
    # Формируем данные для карточек (только температура, влажность, CO2)
    current_data = {}
    for prop in target_props:
        prop_name = prop['name']
        for v in values:
            if v["prop"] == prop_name:
                current_data[prop_name] = {
                    'value': v['value'],
                    'unit': v['unit'],
                    'desc': prop['desc'],
                    'color': prop['color'],
                    'icon': prop['icon']
                }
                break
   
    sensors = list(dashboard_data.keys())
    icon_url = 'https://cdn-icons-png.flaticon.com/512/10338/10338121.png'
    dropdown_html = '''
    <div class="dropdown me-3">
        <button class="btn btn-light dropdown-toggle" type="button" id="sensorDropdown" data-bs-toggle="dropdown" aria-expanded="false">
            Выбрать сенсор
        </button>
        <ul class="dropdown-menu" aria-labelledby="sensorDropdown">
    '''
    for s in sensors:
        safe_s = s.replace(' ', '_').replace('/', '_')
        dropdown_html += f'''
            <li><a class="dropdown-item" href="/dashboard/{safe_s}">
                <img src="{icon_url}" alt="" width="20" height="20" class="me-2">{s.replace('_', ' ')}
            </a></li>
        '''
    dropdown_html += '</ul></div>'
   
    dashboard_html = f'''
<!DOCTYPE html>
<html>
<head>
    <title>Дашборд - {sensor_name.replace('_', ' ')}</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.10.0/font/bootstrap-icons.css">
    <meta charset="utf-8">
    <style>
        body {{ background-color: #f8f9fa; color: #212529; }}
        .navbar-brand {{ font-weight: 700; }}
        .navbar-brand:hover {{ text-decoration: underline; }}
        .navbar-dark.bg-primary {{ background-color: #2F4F4F !important; }}
        .sensor-header {{ display: flex; align-items: center; gap: 15px; margin-left: auto; margin-right: 0; }}
        .sensor-logo {{ width: 42px; height: 42px; border-radius: 8px; background: white; padding: 5px; }}
        .navbar .container-fluid {{ display: flex; justify-content: space-between; align-items: center; }}
        .metrics-container {{ display: flex; gap: 20px; margin-bottom: 25px; flex-wrap: wrap; }}
        .metric-card {{ background: white; border: none; border-radius: 12px; padding: 20px; box-shadow: 0 4px 12px rgba(0,0,0,0.05); flex: 1; min-width: 180px; transition: transform 0.2s, box-shadow 0.2s; }}
        .metric-card:hover {{ transform: translateY(-5px); box-shadow: 0 8px 20px rgba(0,0,0,0.1); }}
        .metric-icon {{ font-size: 2.5rem; margin-bottom: 10px; }}
        .metric-value {{ font-size: 2.2rem; font-weight: bold; margin-bottom: 5px; }}
        .metric-label {{ font-size: 0.95em; opacity: 0.8; }}
        .temp-card {{ background: rgba(200, 162, 200, 0.08); border-left: 4px solid {colors[0]}; }}
        .temp-card .metric-icon {{ color: {colors[0]}; }}
        .humidity-card {{ background: rgba(135, 206, 235, 0.08); border-left: 4px solid {colors[1]}; }}
        .humidity-card .metric-icon {{ color: {colors[1]}; }}
        .co2-card {{ background: rgba(95, 106, 121, 0.08); border-left: 4px solid {colors[2]}; }}
        .co2-card .metric-icon {{ color: {colors[2]}; }}
        .dropdown-container {{ background: white; border-radius: 12px; padding: 20px; margin-bottom: 25px; box-shadow: 0 4px 12px rgba(0,0,0,0.05); }}
        .graph-card {{ background: white; border-radius: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.05); overflow: hidden; display: flex; flex-direction: column; width: 75%; margin: 0 auto 30px auto; min-height: 550px; }}
        .graph-header {{ padding: 15px 20px; border-bottom: 1px solid #eee; }}
        .graph-title {{ font-weight: 600; margin-bottom: 0; }}
        .graph-body {{ padding: 0; flex: 1; }}
        #plotly-graph {{ height: 100% !important; width: 100% !important; }}
        @media (max-width: 768px) {{ .graph-card {{ width: 100%; margin-bottom: 5px; }} }}
    </style>
</head>
<body>
    <nav class="navbar navbar-expand-lg navbar-dark bg-primary">
        <div class="container-fluid">
            <a class="navbar-brand" href="/">← Назад к карте сенсоров</a>
            <div class="sensor-header">
                {dropdown_html}
                <img src="{icon_url}" class="sensor-logo" alt="Sensor">
                <h2 class="text-white mb-0">{sensor_name.replace('_', ' ')}</h2>
            </div>
        </div>
    </nav>
    <div class="container mt-4">
        <div class="metrics-container">
    '''
    for prop_name, data in current_data.items():
        class_name = {"ApparentTemperature": "temp-card", "Humidity": "humidity-card", "CO2": "co2-card"}.get(prop_name, "")
        dashboard_html += f'''
            <div class="metric-card {class_name}">
                <div class="metric-icon"><i class="bi bi-{data['icon']}"></i></div>
                <div class="metric-value">{round(data['value'], 1)}{data['unit']}</div>
                <div class="metric-label">{data['desc']}</div>
            </div>
        '''
    dashboard_html += f'''
        </div>
        <div class="dropdown-container">
            <label for="metrics-select" class="form-label">Выберите параметры для отображения:</label>
            <select class="form-select" id="metrics-select" multiple size="3">
    '''
    for prop in obs_props:
        selected = "selected" if prop["name"] == "ApparentTemperature" else ""
        dashboard_html += f'<option value="{prop["name"]}" {selected}>{prop["desc"]}</option>'
    dashboard_html += f'''
            </select>
        </div>
        <div class="graph-card">
            <div class="graph-header">
                <h5 class="graph-title">Измерения</h5>
            </div>
            <div class="graph-body" id="plotly-graph"></div>
        </div>
    </div>
    <script>
        function updateGraph() {{
            var selectedMetrics = Array.from(document.getElementById('metrics-select').selectedOptions).map(option => option.value);
            if (selectedMetrics.length === 0) {{
                document.getElementById('plotly-graph').innerHTML = '<div class="alert alert-warning">Выберите хотя бы один параметр</div>';
                return;
            }}
            document.getElementById('plotly-graph').innerHTML = '<div class="d-flex justify-content-center py-5"><div class="spinner-border" role="status"><span class="visually-hidden">Загрузка...</span></div></div>';
            var params = new URLSearchParams();
            params.append('metrics', JSON.stringify(selectedMetrics));
            var xhttp = new XMLHttpRequest();
            xhttp.onreadystatechange = function() {{
                if (this.readyState === 4) {{
                    if (this.status === 200) {{
                        try {{
                            var response = JSON.parse(this.responseText);
                            var container = document.getElementById('plotly-graph');
                            container.innerHTML = '';
                            if (!response || response.length === 0) {{
                                container.innerHTML = '<div class="alert alert-warning">Нет данных для отображения</div>';
                                return;
                            }}
                            var traces = response.map(function(metricData) {{
                                return {{
                                    x: metricData.timestamps.map(ts => new Date(ts)),
                                    y: metricData.values,
                                    name: metricData.desc + (metricData.unit ? ' (' + metricData.unit + ')' : ''),
                                    type: 'scatter',
                                    mode: 'lines+markers',
                                    line: {{ color: metricData.color, width: 2 }},
                                    marker: {{ size: 4, color: metricData.color, symbol: 'circle' }},
                                    hovertemplate: '<b>%{{y}}' + (metricData.unit ? ' ' + metricData.unit + ')' : '') + '</b><br>%{{x|%d %b %Y %H:%M}}<extra></extra>',
                                    unit: metricData.unit || ''
                                }};
                            }});
                            var allValues = response.flatMap(metric => metric.values);
                            var minY = Math.min(...allValues);
                            var maxY = Math.max(...allValues);
                            var yRangePadding = (maxY - minY) * 0.1;
                            var defaultYRange = [minY - yRangePadding, maxY + yRangePadding];
                            var layout = {{
                                margin: {{ t: 25, r: 250, b: 100, l: 60 }},
                                font: {{ family: 'Inter', size: 12 }},
                                showlegend: true,
                                legend: {{ x: 1.02, xanchor: 'left', y: 1, bgcolor: 'rgba(255, 255, 255, 0.8)', bordercolor: '#ddd', borderwidth: 1 }},
                                plot_bgcolor: '#ffffff',
                                paper_bgcolor: '#ffffff',
                                xaxis: {{
                                    type: 'date',
                                    tickformat: '%d',
                                    showgrid: true,
                                    gridcolor: '#f0f0f0',
                                    zeroline: false,
                                    tickangle: -30,
                                    tickfont: {{ size: 11 }},
                                    rangeslider: {{ visible: true, bgcolor: '#d3d3d3', bordercolor: '#888', borderwidth: 1, thickness: 0.1 }},
                                    rangeselector: {{
                                        buttons: [
                                            {{ count: 1, label: '1д', step: 'day', stepmode: 'backward' }},
                                            {{ count: 7, label: '7д', step: 'day', stepmode: 'backward' }},
                                            {{ count: 1, label: '1м', step: 'month', stepmode: 'backward' }},
                                            {{ count: 6, label: '6м', step: 'month', stepmode: 'backward' }},
                                            {{ count: 1, label: '1г', step: 'year', stepmode: 'backward' }},
                                            {{ step: 'all', label: 'Всё' }}
                                        ],
                                        x: 0,
                                        xanchor: 'left',
                                        y: 1.1,
                                        yanchor: 'top',
                                        bgcolor: '#d3d3d3',
                                        activecolor: '#888',
                                        bordercolor: '#888',
                                        borderwidth: 1
                                    }},
                                    autorange: true
                                }},
                                yaxis: {{
                                    title: 'Значения',
                                    showgrid: true,
                                    gridcolor: '#f0f0f0',
                                    zeroline: false,
                                    tickfont: {{ size: 11 }},
                                    range: defaultYRange,
                                    rangeselector: {{
                                        buttons: [
                                            {{ label: 'Полный', step: 'all', stepmode: 'backward', range: defaultYRange }},
                                            {{ label: 'Средний', range: [minY + (maxY - minY) * 0.25, maxY - (maxY - minY) * 0.25] }},
                                            {{ label: 'Узкий', range: [minY + (maxY - minY) * 0.4, maxY - (maxY - minY) * 0.4] }}
                                        ],
                                        x: 1.02,
                                        xanchor: 'left',
                                        y: 0.5,
                                        yanchor: 'middle',
                                        orientation: 'vertical',
                                        bgcolor: '#d3d3d3',
                                        activecolor: '#888',
                                        bordercolor: '#888',
                                        borderwidth: 1
                                    }}
                                }},
                                hovermode: 'x unified'
                            }};
                            var config = {{ displayModeBar: true, displaylogo: false, responsive: true, modeBarButtonsToRemove: ['select2d', 'lasso2d', 'autoScale2d', 'resetScale2d'] }};
                            Plotly.newPlot('plotly-graph', traces, layout, config);
                        }} catch(e) {{
                            document.getElementById('plotly-graph').innerHTML = '<div class="alert alert-danger">Ошибка при обработке данных: ' + e.message + '</div>';
                        }}
                    }} else {{
                        document.getElementById('plotly-graph').innerHTML = '<div class="alert alert-danger">Ошибка загрузки данных</div>';
                    }}
                }}
            }};
            xhttp.open("GET", "/api/data/{sensor_name}?" + params.toString(), true);
            xhttp.send();
        }}
        window.addEventListener('load', function() {{ updateGraph(); }});
        document.getElementById('metrics-select').addEventListener('change', updateGraph);
    </script>
</body>
</html>
'''
    return dashboard_html
if __name__ == "__main__":
    app.run(debug=True)
