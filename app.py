import logging
import json
import folium
from folium.plugins import MarkerCluster
from flask import Flask, render_template, request, jsonify
from datetime import datetime, timezone

import config

from services import (
    load_data_from_db,
    dashboard_data,
    get_sensor_data,
    pair_wind,
    build_wind_rose_from_pairs,
    make_safe_key,
    _parse_iso_phen_time,
    _aggregate_by_step,
    _parse_range_cutoff,
    GisService, 
    RASTER_LAYERS, 
    VECTOR_LAYERS, 
    RASTER_BY_NAME, 
    VECTOR_BY_NAME,
    VECTOR_PRESENTATION  # <--- Добавляем импорт
)

# Настройка логирования
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("app")

app = Flask(__name__)
app.config.from_object(config)


# ================= ROUTES =================

@app.route("/")
def root_map():
    # Очистка и загрузка данных (в продакшене лучше использовать кэш)
    dashboard_data.clear()
    try:
        locations_map = load_data_from_db()
    except Exception as e:
        logger.error(f"Failed to load sensor data: {e}")
        locations_map = {}

    # Создаем карту
    m = folium.Map(location=(55.7558, 37.6175), zoom_start=12, tiles='CartoDB positron')

    # Инъекция ресурсов (CSS/JS/Controls) через шаблоны partials
    inject_map_assets(m)

    # Кластеризация маркеров
    marker_cluster = MarkerCluster().add_to(m)
    icon_url = 'https://cdn-icons-png.flaticon.com/512/10338/10338121.png'

    # Создание маркеров
    for loc_id, loc_data in locations_map.items():
        if loc_data["lat"] is None or loc_data["lon"] is None:
            continue

        things = list(loc_data["things"].values())
        if not things:
            continue

        # Генерируем HTML для попапа
        popup_html = generate_popup_html(loc_id, loc_data, things)

        folium.Marker(
            location=(loc_data["lat"], loc_data["lon"]),
            popup=folium.Popup(popup_html, max_width=360, min_width=320),
            tooltip=loc_data["name"],
            icon=folium.CustomIcon(icon_url, icon_size=(32, 32), icon_anchor=(16, 32), popup_anchor=(0, -32))
        ).add_to(marker_cluster)

    return m.get_root().render()


@app.route("/dashboard/<sensor_key>")
def dashboard(sensor_key):
    sensor = get_sensor_data(sensor_key)
    if not sensor:
        return f"<h3>Нет данных для {sensor_key}</h3>", 404

    # Подготовка данных
    dm_series = sensor.get("dm_series", [])
    sm_series = sensor.get("sm_series", [])
    wind_pairs = pair_wind(dm_series, sm_series)
    has_wind = bool(wind_pairs)

    rose = build_wind_rose_from_pairs(wind_pairs) if has_wind else {"theta": [], "r": [], "c": []}

    last_dm = wind_pairs[0][1] if has_wind else None
    last_sm = wind_pairs[0][2] if has_wind else None

    dir_str = "—"
    if has_wind:
        dirs = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']
        idx = int(((last_dm % 360) + 11.25) // 22.5) % 16
        dir_str = f"{int(round(last_dm))}° ({dirs[idx]})"

    sensors_list = [
        {"key": k, "title": get_sensor_data(k).get("title", k.replace('_', ' '))}
        for k in dashboard_data.keys()
    ]

    current_values = {}
    values = sensor.get("values", [])
    for tcfg in sensor.get("target_props", []):
        prop_vals = [vv for vv in values if vv["prop"] == tcfg['name']]
        if prop_vals:
            prop_vals.sort(key=lambda x: x['timestamp'], reverse=True)
            v = prop_vals[0]
            current_values[tcfg['name']] = {
                "value": v["value"],
                "unit": tcfg["unit"],
                "desc": tcfg["desc"],
                "icon": tcfg["icon"]
            }

    return render_template(
        "dashboard.html",
        title=sensor.get("title", sensor_key),
        sensors=sensors_list,
        icon_url='https://cdn-icons-png.flaticon.com/512/10338/10338121.png',
        current=current_values,
        has_wind=has_wind,
        last_dm=last_dm,
        last_sm=last_sm,
        dir_str=dir_str,
        rose_theta=rose["theta"],
        rose_r=rose["r"],
        rose_c=rose["c"],
        obs_props=sensor.get("obs_props", []),
        sensor_key=sensor_key,
        DARK_GREEN=config.DARK_GREEN,
        PALE_BLUE=config.PALE_BLUE,
        SLATE=config.SLATE,
        colors=config.COLORS
    )


# ================= API ROUTES =================

@app.get("/api/gis/raster")
def api_gis_raster():
    schema = request.args.get("schema", "rasters")
    table = request.args.get("table")
    if not table: return jsonify({"error": "table required"}), 400

    meta = RASTER_BY_NAME.get((schema, table))
    if not meta: return jsonify({"error": "unknown raster table"}), 404

    try:
        out = GisService.render_raster_png(schema, table, meta["rast_col"])
        if not out: return jsonify({"error": "empty raster"}), 404
        return jsonify(out)
    except Exception as e:
        logger.exception("Raster render failed")
        return jsonify({"error": str(e)}), 500


@app.get("/api/gis/geojson")
def api_gis_geojson():
    schema = request.args.get("schema", "public")
    table = request.args.get("table")
    limit = int(request.args.get("limit", GisService.DEFAULT_VECTOR_LIMIT))
    tol = float(request.args.get("simplify", GisService.DEFAULT_SIMPLIFY_TOLERANCE))

    if not table: return jsonify({"type": "FeatureCollection", "features": []})

    meta = VECTOR_BY_NAME.get((schema, table))
    if not meta: return jsonify({"type": "FeatureCollection", "features": []})

    try:
        gj = GisService.vector_geojson(schema, table, meta["geom_col"], limit, tol)
        return jsonify(gj if isinstance(gj, dict) else {"type": "FeatureCollection", "features": []})
    except Exception:
        logger.exception("GeoJSON failed")
        return jsonify({"type": "FeatureCollection", "features": []})


@app.route("/api/data/<sensor_key>")
def api_sensor_data(sensor_key):
    sensor = get_sensor_data(sensor_key)
    if not sensor: return json.dumps([])

    values = sensor['values']
    obs_props = sensor['obs_props']

    metrics_str = request.args.get('metrics')
    if not metrics_str: return json.dumps([])

    try:
        selected = json.loads(metrics_str)
        if not isinstance(selected, list): selected = [selected]
    except Exception:
        selected = [metrics_str]

    range_str = request.args.get('range', '7d')
    agg_str = request.args.get('agg', '1h')
    cutoff_dt = _parse_range_cutoff(range_str)

    agg_map = {"1h": 60, "3h": 180, "1d": 1440}
    agg_key = (agg_str or "1h").lower()
    step_minutes = 60 if agg_key in ("auto", "raw") else agg_map.get(agg_key, 60)

    result = []

    for prop_name in selected:
        prop_data_all = [v for v in values if v["prop"] == prop_name]
        if not prop_data_all: continue

        if cutoff_dt:
            prop_data = []
            for d in prop_data_all:
                dt = _parse_iso_phen_time(d.get("timestamp"))
                if dt and dt >= cutoff_dt: prop_data.append(d)
        else:
            prop_data = prop_data_all

        if not prop_data:
            prop_data = sorted(prop_data_all, key=lambda d: d.get("timestamp"))[-200:]
            if not prop_data: continue

        prop_info = next((p for p in obs_props if p["name"] == prop_name),
                         {"desc": prop_name, "unit": "", "color": "#999999"})
        ts_list, val_list = _aggregate_by_step(prop_data, step_minutes)

        if not ts_list and prop_data:
            prop_data_sorted = sorted(prop_data, key=lambda d: d.get("timestamp"))
            ts_list = [d["timestamp"] for d in prop_data_sorted]
            val_list = [d["value"] for d in prop_data_sorted]

        result.append({
            "prop": prop_name, "timestamps": ts_list, "values": val_list,
            "desc": prop_info["desc"], "color": prop_info.get("color", "#999999"), "unit": prop_info["unit"]
        })

    return json.dumps(result)

# ================= HELPERS =================

def inject_map_assets(m):
    """
    Вставка CSS/JS в Folium карту.
    """
    css_html = render_template(
        "map_partials/css_inject.html",
        raster_layers=RASTER_LAYERS,
        vector_layers=VECTOR_LAYERS, # Оставляем для совместимости, если нужно
        vector_presentation=VECTOR_PRESENTATION, # Новая структура
        safe_whitelist=GisService.SAFE_VECTOR_WHITELIST
    )
    m.get_root().html.add_child(folium.Element(css_html))

    js_html = render_template("map_partials/js_inject.html")
    m.get_root().html.add_child(folium.Element(js_html))


def generate_popup_html(loc_id, loc_data, things):
    """Генерация HTML контента для всплывающего окна (Popup) на карте."""
    location_name = loc_data["name"]
    container_id = f"LOC-{loc_id}"

    popup_html = [f'<div id="{container_id}" class="sensor-popup"><h4>{location_name}</h4>']

    # Радио-кнопки
    popup_html.append('<div class="radio-block">')
    for i, th in enumerate(things):
        tid = th['id']
        safe_tid = make_safe_key(str(tid))
        tname = th['name']
        checked = 'checked' if i == 0 else ''
        popup_html.append(f"""
            <div class="form-check">
                <input class="form-check-input" type="radio" name="thing-{container_id}" id="thing-{safe_tid}" {checked}
                       onclick="switchThing('{container_id}', '{safe_tid}')">
                <label class="form-check-label" for="thing-{safe_tid}">{tname}</label>
            </div>
        """)
    popup_html.append('</div>')

    # Блоки с метриками
    for i, th in enumerate(things):
        tid = th['id']
        safe_tid = make_safe_key(str(tid))
        key = th['dashboard_key']
        latest = th['latest']
        display = "block" if i == 0 else "none"

        popup_html.append(f'<div id="metrics-thing-{safe_tid}" class="thing-metrics" style="display:{display}">')

        if not latest:
            popup_html.append('<p class="text-muted mb-2">Нет данных за этот период</p>')
        else:
            popup_html.append('<div class="mini-metrics">')
            sensor_data = get_sensor_data(key)
            target_props = sensor_data.get('target_props', []) if sensor_data else []

            for prop_name, (val, unit) in latest.items():
                conf = next((p for p in target_props if p['name'] == prop_name), None)
                if not conf: continue

                cls_name = f"mini-{prop_name.replace('.', '_')}"
                val_str = f"{round(val, 1)}{unit}" if val is not None else "—"

                popup_html.append(f"""
                    <div class="mini-metric {cls_name}">
                        <div class="mini-icon"><i class="bi bi-{conf.get('icon', 'activity')}"></i></div>
                        <div class="mini-value">{val_str}</div>
                        <div class="mini-label">{conf['desc']}</div>
                    </div>
                """)
            popup_html.append('</div>')

        if get_sensor_data(key) and get_sensor_data(key).get("values"):
            popup_html.append(
                f'<a class="dashboard-btn dash-btn" id="btn-thing-{safe_tid}" href="/dashboard/{key}" style="display:{display}">Дашборд</a>')

        popup_html.append('</div>')

    popup_html.append('</div>')
    return "".join(popup_html)


if __name__ == "__main__":
    app.run(host="0.0.0.0", debug=True, port=int(config.os.getenv("PORT")))