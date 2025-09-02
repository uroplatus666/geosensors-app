import folium
from folium.plugins import MarkerCluster
from flask import Flask, render_template_string, request
import requests
from datetime import datetime, timedelta
import json
import logging

# Настройка логирования для диагностики
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config["CACHE_TYPE"] = "null"

# Глобальное хранилище данных для дашбордов
dashboard_data = {}

def get_sensor_data(thing_id, thing_name):
    """Функция для получения данных сенсора"""
    # Список параметров, соответствующих Datastreams
    obs_props = [
        {"name": "ApparentTemperature", "desc": "Ощущаемая температура воздуха (°C)", "color": "#C8A2C8", "unit": "°C"},
        {"name": "Humidity", "desc": "Влажность воздуха (%)", "color": "#87CEEB", "unit": "%"},
        {"name": "CO2", "desc": "Концентрация CO2 (ppm)", "color": "#5F6A79", "unit": "ppm"}
    ]

    values = []

    # Запрос Datastreams для Thing
    datastreams_url = f"http://90.156.134.128:8080/FROST-Server/v1.1/Things({thing_id})/Datastreams"
    try:
        logger.debug(f"Отправка запроса к API: {datastreams_url}")
        response = requests.get(datastreams_url)
        response.raise_for_status()
        datastreams_data = response.json()
        logger.debug(f"Полученные Datastreams для Thing {thing_id}: {json.dumps(datastreams_data, indent=2)}")
    except requests.RequestException as e:
        logger.error(f"Ошибка при запросе Datastreams для Thing {thing_id}: {e}")
        return []

    for datastream in datastreams_data.get("value", []):
        datastream_name = datastream.get("name", "")
        logger.debug(f"Обработка Datastream: {datastream_name}")

        # Сопоставляем Datastream с параметром
        prop = None
        if "Ощущаемая температура" in datastream_name:
            prop = obs_props[0]
        elif "Влажность воздуха" in datastream_name:
            prop = obs_props[1]
        elif "Концентрация CO2" in datastream_name:
            prop = obs_props[2]
        else:
            logger.warning(f"Datastream '{datastream_name}' не соответствует ни одному параметру")
            continue

        # Запрос Observations для Datastream
        observations_url = (
            f"http://90.156.134.128:8080/FROST-Server/v1.1/Datastreams({datastream.get('@iot.id')})/Observations"
            f"?$top=100&$orderby=phenomenonTime%20desc&$filter=phenomenonTime%20ge%20"
            f"{(datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')}T00:00:00Z"
        )
        try:
            logger.debug(f"Отправка запроса к API: {observations_url}")
            response = requests.get(observations_url)
            response.raise_for_status()
            observations_data = response.json()
            logger.debug(f"Полученные Observations для Datastream {datastream.get('@iot.id')}: {json.dumps(observations_data, indent=2)}")
        except requests.RequestException as e:
            logger.error(f"Ошибка при запросе Observations для Datastream {datastream.get('@iot.id')}: {e}")
            continue

        observations = observations_data.get("value", [])
        if not observations:
            logger.warning(f"Нет наблюдений для Datastream: {datastream_name}")
            continue

        for obs in observations:
            result = obs.get("result")
            if result is None or result == 0:
                logger.warning(f"Пропуск наблюдения с result={result}: {obs}")
                continue
            values.append({
                "timestamp": obs["phenomenonTime"],
                "prop": prop["name"],
                "prop_desc": prop["desc"],
                "value": float(result),
                "color": prop["color"],
                "unit": prop["unit"]
            })

    return values

def get_temperature_stats(values):
    temp_values = [v for v in values if v["prop"] == "ApparentTemperature"]
    if not temp_values:
        return None

    current_temp = temp_values[0]["value"] if temp_values else None
    temp_numbers = [v["value"] for v in temp_values]

    if not temp_numbers:
        return None

    min_temp = min(temp_numbers)
    max_temp = max(temp_numbers)
    avg_temp = sum(temp_numbers) / len(temp_numbers)

    return {
        "current": round(current_temp, 1) if current_temp is not None else None,
        "min": round(min_temp, 1),
        "max": round(max_temp, 1),
        "avg": round(avg_temp, 1)
    }

def get_latest(values, prop_name):
    """Получить последнее значение по параметру"""
    for v in values:
        if v["prop"] == prop_name:
            return v["value"], v.get("unit", "")
    return None, ""

@app.route("/")
def generate_root_page():
    global dashboard_data

    # Центр карты — координаты Москвы
    m = folium.Map(
        location=(55.7558, 37.6175),
        zoom_start=16,
        tiles='CartoDB positron',
        min_zoom=10,
        max_zoom=19,
        max_bounds=True,  # Ограничиваем границы
        max_bounds_viscosity=1.0  # Запрещаем выход за границы
    )


    # Подключаем Bootstrap, Bootstrap Icons и Google Fonts + дополнительные стили
    m.get_root().header.add_child(folium.Element("""
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@500;700&family=Poppins:wght@600;700&display=swap" rel="stylesheet">
        <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
        <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
        <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.10.0/font/bootstrap-icons.css">
        <style>
            .sensor-popup h4 {
                font-family: 'Poppins', 'Inter', system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
                font-weight: 700;
                font-size: 1.6em;
                margin-bottom: 10px;
                letter-spacing: .2px;
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
                background: rgba(0,0,0,0.06);
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
            .mini-icon {
                font-size: 2.2em;
                margin-bottom: 5px;
            }
            .mini-temp .mini-icon { color: #C8A2C8; }
            .mini-hum .mini-icon { color: #87CEEB }
            .mini-co2 .mini-icon { color: #5F6A79; }
            .mini-value {
                font-size: 1.3em;
                font-weight: 700;
            }
            .mini-label {
                font-size: 0.85em;
                opacity: 0.7;
            }
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
        </style>
    """))

    # Список параметров, соответствующих Datastreams
    obs_props = [
        {"name": "ApparentTemperature", "desc": "Ощущаемая температура воздуха (°C)", "color": "#C8A2C8"},
        {"name": "Humidity", "desc": "Влажность воздуха (%)", "color": "#87CEEB"},
        {"name": "CO2", "desc": "Концентрация CO2 (ppm)", "color": "#5F6A79"}
    ]

    # Создаем кластер для маркеров
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

    # Запрос Things
    things_url = "http://90.156.134.128:8080/FROST-Server/v1.1/Things"
    try:
        logger.debug(f"Отправка запроса к API: {things_url}")
        response = requests.get(things_url)
        response.raise_for_status()
        things_data = response.json()
        logger.debug(f"Полученные Things: {json.dumps(things_data, indent=2)}")
    except requests.RequestException as e:
        logger.error(f"Ошибка при запросе Things: {e}")
        return f"Ошибка при запросе Things: {e}"

    for thing in things_data.get("value", []):
        thing_has_data = False
        thing_id = thing.get("@iot.id")
        thing_name = thing.get("name", "Unknown Thing")

        # Получаем данные для дашборда
        values = get_sensor_data(thing_id, thing_name)
        if values:
            thing_has_data = True
            # Сохраняем данные для последующего использования в дашборде
            dashboard_data[thing_name] = {
                'values': values,
                'obs_props': obs_props
            }

        coordinates = None
        # Получаем координаты из первого datastream с observedArea
        datastreams_url = f"http://90.156.134.128:8080/FROST-Server/v1.1/Things({thing_id})/Datastreams"
        try:
            response = requests.get(datastreams_url)
            response.raise_for_status()
            datastreams_data = response.json()
            for datastream in datastreams_data.get("value", []):
                observed_area = datastream.get("observedArea")
                if observed_area and observed_area.get("type") == "Point" and not coordinates:
                    coordinates = observed_area.get("coordinates")
                    break
        except requests.RequestException as e:
            logger.error(f"Ошибка при запросе координат для Thing {thing_id}: {e}")

        if not coordinates:
            logger.warning(f"Нет координат для Thing: {thing_name}")
            continue

        # Получаем текущие значения для мини-метрик
        temp_val, temp_unit = get_latest(values, "ApparentTemperature")
        hum_val, hum_unit = get_latest(values, "Humidity")
        co2_val, co2_unit = get_latest(values, "CO2")

        # Создаем содержимое попапа
        safe_thing_name = thing_name.replace(' ', '_').replace('/', '_')

        popup_content = f'''
        <div class="sensor-popup">
            <h4>{thing_name}</h4>
            <div class="mini-metrics">
                <div class="mini-metric mini-temp">
                    <div class="mini-icon"><i class="bi bi-thermometer-half"></i></div>
                    <div class="mini-value">{round(temp_val,1) if temp_val is not None else ''}{temp_unit if temp_val is not None else ''}</div>
                    <div class="mini-label">Температура</div>
                </div>
                <div class="mini-metric mini-hum">
                    <div class="mini-icon"><i class="bi bi-droplet"></i></div>
                    <div class="mini-value">{round(hum_val,1) if hum_val is not None else ''}{hum_unit if hum_val is not None else ''}</div>
                    <div class="mini-label">Влажность</div>
                </div>
                <div class="mini-metric mini-co2">
                    <div class="mini-icon"><i class="bi bi-cloud-haze2"></i></div>
                    <div class="mini-value">{int(co2_val) if co2_val is not None else ''}{co2_unit if co2_val is not None else ''}</div>
                    <div class="mini-label">CO2</div>
                </div>
            </div>
            <a href="/dashboard/{safe_thing_name}" class="dashboard-btn" target="_blank">Открыть дашборд</a>
        </div>
        '''

        popup = folium.Popup(popup_content, max_width=340, min_width=300)
        # Кастомная иконка сенсора
        sensor_icon = folium.CustomIcon(
            icon_image='https://cdn-icons-png.flaticon.com/512/10338/10338121.png',
            icon_size=(32, 32),
            icon_anchor=(16, 32),
            popup_anchor=(0, -32)
        )
        marker = folium.Marker(
            location=(coordinates[1], coordinates[0]),
            popup=popup,
            tooltip=thing_name,
            icon=sensor_icon
        )

        if thing_has_data:
            logger.debug(f"Добавление маркера на карту для Thing: {thing_name}")
            marker.add_to(marker_cluster)

    m.save("output/index.htm")
    return render_template_string(m._repr_html_())

@app.route("/api/data/<sensor_name>")
def get_api_data(sensor_name):
    """API-эндпоинт для получения данных графика"""
    original_sensor_name = sensor_name.replace('_', ' ')

    if original_sensor_name not in dashboard_data:
        return json.dumps([])

    sensor_data = dashboard_data[original_sensor_name]
    values = sensor_data['values']
    obs_props = sensor_data['obs_props']

    metrics_str = request.args.get('metrics')
    if not metrics_str:
        return json.dumps([])

    try:
        selected_props = json.loads(metrics_str)
        if not isinstance(selected_props, list):
            selected_props = [selected_props]
    except json.JSONDecodeError:
        return json.dumps([])

    if not isinstance(selected_props, list) or not selected_props:
        return json.dumps([])

    result = []
    for prop_name in selected_props:
        prop_data = [{"timestamp": v["timestamp"], "value": v["value"]} for v in values if v["prop"] == prop_name]
        if prop_data:
            prop_info = next((p for p in obs_props if p["name"] == prop_name), {"desc": prop_name, "color": "#999"})
            result.append({
                "prop": prop_name,
                "timestamps": [d["timestamp"] for d in prop_data],
                "values": [d["value"] for d in prop_data],
                "desc": prop_info["desc"],
                "color": prop_info["color"],
                "unit": prop_info.get("unit", "")
            })

    return json.dumps(result)

@app.route("/dashboard/<sensor_name>")
def dashboard(sensor_name):
    """Страница дашборда для конкретного сенсора"""
    original_sensor_name = sensor_name.replace('_', ' ')

    if original_sensor_name not in dashboard_data:
        return f"<h1>Данные для сенсора '{original_sensor_name}' не найдены</h1>", 404

    sensor_data = dashboard_data[original_sensor_name]
    values = sensor_data['values']
    obs_props = sensor_data['obs_props']

    current_data = {}
    for prop in obs_props:
        prop_name = prop['name']
        value, unit = get_latest(values, prop_name)
        if value is not None:
            current_data[prop_name] = {
                'value': value,
                'unit': unit,
                'desc': prop['desc'],
                'color': prop['color']
            }

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
                <img src="{icon_url}" alt="" width="20" height="20" class="me-2">{s}
            </a></li>
        '''
    dropdown_html += '''
        </ul>
    </div>
    '''

    dashboard_html = f'''
<!DOCTYPE html>
<html>
<head>
    <title>Дашборд - {original_sensor_name}</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.10.0/font/bootstrap-icons.css">
    <meta charset="utf-8">
    <style>
        body {{
            background-color: #f8f9fa;
            color: #212529;
        }}
        .navbar-brand {{
            font-weight: 700;
        }}
        .navbar-brand:hover {{
            text-decoration: underline;
        }}
        .navbar-dark.bg-primary {{
            background-color: #2F4F4F !important;
        }}
        .sensor-header {{
            display: flex;
            align-items: center;
            gap: 15px;
            margin-left: auto;
            margin-right: 0;
        }}
        .sensor-logo {{
            width: 42px;
            height: 42px;
            border-radius: 8px;
            background: white;
            padding: 5px;
        }}
        .navbar .container-fluid {{
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .metrics-container {{
            display: flex;
            gap: 20px;
            margin-bottom: 25px;
            flex-wrap: wrap;
        }}
        .metric-card {{
            background: white;
            border: none;
            border-radius: 12px;
            padding: 20px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.05);
            flex: 1;
            min-width: 180px;
            transition: transform 0.2s, box-shadow 0.2s;
        }}
        .metric-card:hover {{
            transform: translateY(-5px);
            box-shadow: 0 8px 20px rgba(0,0,0,0.1);
        }}
        .metric-icon {{
            font-size: 2.5rem;
            margin-bottom: 10px;
        }}
        .metric-value {{
            font-size: 2.2rem;
            font-weight: bold;
            margin-bottom: 5px;
        }}
        .metric-label {{
            font-size: 0.95em;
            opacity: 0.8;
        }}
        .temp-card {{
            background: rgba(200, 162, 200, 0.08);
            border-left: 4px solid #C8A2C8;
        }}
        .humidity-card {{
            background: rgba(135, 206, 235, 0.08);
            border-left: 4px solid #87CEEB;
        }}
        .co2-card {{
            background: rgba(95, 106, 121, 0.08);
            border-left: 4px solid #5F6A79;
        }}
        .dropdown-container {{
            background: white;
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 25px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.05);
        }}
        .graph-card {{
            background: white;
            border-radius: 12px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.05);
            overflow: hidden;
            display: flex;
            flex-direction: column;
            width: 75%;
            margin: 0 auto 30px auto;
            min-height: 550px;
        }}
        .graph-header {{
            padding: 15px 20px;
            border-bottom: 1px solid #eee;
        }}
        .graph-title {{
            font-weight: 600;
            margin-bottom: 0;
        }}
        .graph-body {{
            padding: 0;
            flex: 1;
        }}
        #plotly-graph {{
            height: 100% !important;
            width: 100% !important;
        }}
        @media (max-width: 768px) {{
            .graph-card {{
                width: 100%;
                margin-bottom: 5px;
            }}
        }}
    </style>
</head>
<body>
    <nav class="navbar navbar-expand-lg navbar-dark bg-primary">
        <div class="container-fluid">
            <a class="navbar-brand" href="/">← Назад к карте сенсоров</a>
            <div class="sensor-header">
                {dropdown_html}
                <img src="{icon_url}" class="sensor-logo" alt="Sensor">
                <h2 class="text-white mb-0">{original_sensor_name}</h2>
            </div>
        </div>
    </nav>
    <div class="container mt-4">
        <!-- Карточки с текущими значениями -->
        <div class="metrics-container">
    '''

    # Карточки для текущих значений
    for prop_name, data in current_data.items():
        class_name = ""
        if prop_name == "ApparentTemperature":
            class_name = "temp-card"
        elif prop_name == "Humidity":
            class_name = "humidity-card"
        elif prop_name == "CO2":
            class_name = "co2-card"
        dashboard_html += f'''
            <div class="metric-card {class_name}">
                <div class="metric-icon">
                    <i class="bi bi-{'thermometer-half' if prop_name == 'ApparentTemperature' else 'droplet' if prop_name == 'Humidity' else 'cloud-haze2'}"></i>
                </div>
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
        // Функция для обновления графика
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
                            console.log('API Response:', response);
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
                                    hovertemplate: '<b>%{{y}}' + (metricData.unit ? ' ' + metricData.unit : '') + '</b><br>%{{x|%d %b %Y %H:%M}}<extra></extra>',
                                    unit: metricData.unit || ''
                                }};
                            }});

                            // Вычисляем минимальные и максимальные значения для всех метрик
                            var allValues = response.flatMap(metric => metric.values);
                            var minY = Math.min(...allValues);
                            var maxY = Math.max(...allValues);
                            var yRangePadding = (maxY - minY) * 0.1; // Добавляем 10% отступа
                            var defaultYRange = [minY - yRangePadding, maxY + yRangePadding];


                            var layout = {{
                                margin: {{ t: 25, r: 250, b: 100, l: 60 }}, // Увеличен нижний отступ для rangeslider
                                font: {{ family: 'Inter', size: 12 }},
                                showlegend: true,
                                legend: {{
                                    x: 1.02, // Размещение легенды справа от графика
                                    xanchor: 'left',
                                    y: 1,
                                    bgcolor: 'rgba(255, 255, 255, 0.8)',
                                    bordercolor: '#ddd',
                                    borderwidth: 1
                                }},
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
                                    rangeslider: {{
                                        visible: true,
                                        bgcolor: '#d3d3d3',
                                        bordercolor: '#888',
                                        borderwidth: 1,
                                        thickness: 0.1
                                    }},
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
                                    range: defaultYRange, // Устанавливаем начальный диапазон
                                    rangeselector: {{
                                        buttons: [
                                            {{
                                                label: 'Полный',
                                                step: 'all',
                                                stepmode: 'backward',
                                                range: defaultYRange
                                            }},
                                            {{
                                                label: 'Средний',
                                                range: [
                                                    minY + (maxY - minY) * 0.25,
                                                    maxY - (maxY - minY) * 0.25
                                                ]
                                            }},
                                            {{
                                                label: 'Узкий',
                                                range: [
                                                    minY + (maxY - minY) * 0.4,
                                                    maxY - (maxY - minY) * 0.4
                                                ]
                                            }}
                                        ],
                                        x: 1.02,
                                        xanchor: 'left',
                                        y: 0.5,
                                        yanchor: 'middle',
                                        orientation: 'vertical', // Вертикальное расположение кнопок
                                        bgcolor: '#d3d3d3',
                                        activecolor: '#888',
                                        bordercolor: '#888',
                                        borderwidth: 1
                                    }}
                                }},
                                hovermode: 'x unified'
                            }};

                            var config = {{
                                displayModeBar: true,
                                displaylogo: false,
                                responsive: true,
                                modeBarButtonsToRemove: ['select2d', 'lasso2d', 'autoScale2d', 'resetScale2d']
                            }};

                            Plotly.newPlot('plotly-graph', traces, layout, config).then(function(gd) {{
                                gd.on('plotly_relayout', function(eventdata) {{
                                    if (eventdata['xaxis.range[0]'] && eventdata['xaxis.range[1]']) {{
                                        var start = new Date(eventdata['xaxis.range[0]']);
                                        var end = new Date(eventdata['xaxis.range[1]']);
                                        var diffDays = (end - start) / (1000 * 60 * 60 * 24);

                                        var newTickFormat;
                                        if (diffDays <= 7) {{
                                            newTickFormat = '%d';
                                        }} else if (diffDays <= 90) {{
                                            newTickFormat = '%b';
                                        }} else {{
                                            newTickFormat = '%Y';
                                        }}

                                        Plotly.relayout('plotly-graph', {{
                                            'xaxis.tickformat': newTickFormat
                                        }});
                                    }}
                                    // Обработка изменения диапазона Y
                                    if (eventdata['yaxis.range[0]'] && eventdata['yaxis.range[1]']) {{
                                        console.log('Y-axis range updated:', eventdata['yaxis.range[0]'], eventdata['yaxis.range[1]']);
                                    }}
                                }});
                            }});
                        }} catch(e) {{
                            console.error('Error rendering graphs:', e);
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

        window.addEventListener('load', function() {{
            updateGraph();
        }});
        document.getElementById('metrics-select').addEventListener('change', updateGraph);
    </script>
</body>
</html>
'''

    return dashboard_html

if __name__ == "__main__":
    app.run(debug=True)