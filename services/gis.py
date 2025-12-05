import io
import json
import base64
import logging
import psycopg2
import psycopg2.extras
import numpy as np
from psycopg2 import sql
from PIL import Image
from rasterio.io import MemoryFile
from functools import lru_cache

# Импортируем конфиг
import config

logger = logging.getLogger("app.gis")

# Импортируем конфигурации из config.py
VECTOR_PRESENTATION = config.VECTOR_PRESENTATION
RASTER_METADATA = config.RASTER_METADATA
COLOR_RAMPS = config.COLOR_RAMPS

class GisService:
    """Сервис для инкапсуляции работы с GIS базой данных."""

    SAFE_VECTOR_WHITELIST = {
        "boundary_campus", "lulc_campus", "active_tt_campus",
        "monitoring_points_campus", "sampling_campus", "tree_inventory_campus"
    }
    DEFAULT_VECTOR_LIMIT = 20000
    DEFAULT_SIMPLIFY_TOLERANCE = 0.0001

    @staticmethod
    def get_connection():
        return psycopg2.connect(
            host=config.GIS_DB_HOST,
            port=config.GIS_DB_PORT,
            dbname=config.GIS_DB_NAME,
            user=config.GIS_DB_USER,
            password=config.GIS_DB_PASS,
            options="-c default_transaction_read_only=on -c statement_timeout=300000"
        )

    @staticmethod
    def list_rasters():
        """
        Получение списка растров. Переименовывает их согласно config.RASTER_METADATA.
        """
        q = """
        SELECT table_schema AS schema, table_name AS name, column_name AS rast_col
        FROM information_schema.columns
        WHERE table_schema='rasters' AND data_type='USER-DEFINED' 
          AND (udt_name='raster' OR udt_name LIKE '%raster%')
        ORDER BY table_name, column_name;
        """
        try:
            with GisService.get_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(q)
                    rows = cur.fetchall()
                    
                    # Если пусто, пробуем другой запрос (fallback)
                    if not rows:
                        cur.execute("""
                            SELECT table_schema AS schema, table_name AS name, 'rast' AS rast_col
                            FROM information_schema.tables
                            WHERE table_schema='rasters' AND table_type='BASE TABLE'
                            ORDER BY table_name;
                        """)
                        rows = cur.fetchall()
                    
                    # Обогащаем данными из конфига (Title, Unit)
                    results = []
                    for r in rows:
                        key = (r['schema'], r['name'])
                        meta = RASTER_METADATA.get(key)
                        if meta:
                            r['title'] = meta['title']
                            r['unit'] = meta['unit']
                            results.append(r)
                        # Если слоя нет в конфиге, можно раскомментировать строки ниже, чтобы показывать "как есть"
                        # else:
                        #    r['title'] = r['name']
                        #    results.append(r)
                    
                    # Сортируем по русскому названию
                    return sorted(results, key=lambda x: x['title'])

        except Exception as e:
            logger.error(f"GIS DB Connection failed (rasters): {e}")
            return []

    @staticmethod
    def list_vectors():
        # (Без изменений)
        try:
            with GisService.get_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("SELECT to_regclass('public.geometry_columns') IS NOT NULL AS has_gc;")
                    has_gc = bool(cur.fetchone().get('has_gc', False))
                    if has_gc:
                        cur.execute("""
                            SELECT f_table_schema AS schema, f_table_name AS name, f_geometry_column AS geom_col
                            FROM public.geometry_columns WHERE f_table_schema='public'
                            ORDER BY f_table_schema, f_table_name;
                        """)
                        rows = cur.fetchall()
                        if rows: return rows
                    cur.execute("""
                        SELECT table_schema AS schema, table_name AS name, column_name AS geom_col
                        FROM information_schema.columns
                        WHERE table_schema='public' AND data_type='USER-DEFINED' AND udt_name='geometry'
                        ORDER BY table_name, column_name;
                    """)
                    return cur.fetchall()
        except Exception as e:
            logger.error(f"GIS DB Connection failed (vectors): {e}")
            return []

    @staticmethod
    def _hex_to_rgb(hex_color):
        hex_color = hex_color.lstrip('#')
        return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))

    @staticmethod
    def _create_lut(ramp_name):
        """Создает таблицу цветов (Look Up Table) для градиента."""
        colors_hex = COLOR_RAMPS.get(ramp_name, COLOR_RAMPS["default"])
        colors_rgb = [GisService._hex_to_rgb(c) for c in colors_hex]
        
        n_stops = len(colors_rgb)
        lut = np.zeros((256, 3), dtype=np.uint8)
        
        steps = np.linspace(0, 255, n_stops)
        
        for i in range(n_stops - 1):
            c1 = np.array(colors_rgb[i])
            c2 = np.array(colors_rgb[i+1])
            idx_start = int(steps[i])
            idx_end = int(steps[i+1])
            length = idx_end - idx_start
            
            for j in range(length):
                t = j / length
                lut[idx_start + j] = (c1 * (1 - t) + c2 * t).astype(np.uint8)
        
        lut[-1] = np.array(colors_rgb[-1], dtype=np.uint8)
        return lut

    @staticmethod
    def _process_raster_data(tiff_bytes: bytes, ramp_name: str) -> dict:
        """Конвертирует TIFF в PNG, применяет палитру и считает статистику для легенды."""
        with MemoryFile(tiff_bytes) as mem:
            with mem.open() as ds:
                bands = ds.read(masked=True)
                
                # RGB или RGBA (Ортофотопланы) - отдаем как есть
                if ds.count >= 3:
                    if ds.count == 3:
                        rgb = np.ma.stack(bands, axis=-1)
                        mask = np.any(bands.mask, axis=0)
                        alpha = (~mask) * 255
                        rgba_data = np.dstack((np.ma.getdata(rgb), alpha))
                    else:
                        rgba = np.ma.stack(bands, axis=-1)
                        rgba_data = np.ma.getdata(rgba)
                    
                    # Нормализация, если не байт
                    if rgba_data.dtype != 'uint8':
                        rgba_data = rgba_data.astype(float)
                        for i in range(3):
                            ch = rgba_data[..., i]
                            mn, mx = ch.min(), ch.max()
                            if mx > mn: ch = (ch - mn) / (mx - mn) * 255
                            rgba_data[..., i] = ch
                        if ds.count == 4:
                            rgba_data[..., 3] = np.clip(rgba_data[..., 3] * 255, 0, 255)
                    
                    img = Image.fromarray(rgba_data.astype(np.uint8), mode="RGBA")
                    buf = io.BytesIO()
                    img.save(buf, format="PNG")
                    return {
                        "png_bytes": buf.getvalue(),
                        "stats": {"ramp": "rgb"} # Легенда не нужна
                    }

                # Один канал (Данные) - применяем палитру
                elif ds.count == 1:
                    arr = bands[0]
                    data = np.ma.getdata(arr).astype(float)
                    mask = np.ma.getmask(arr)
                    valid = data[~mask]
                    
                    if valid.size == 0:
                        img = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
                        buf = io.BytesIO(); img.save(buf, format="PNG")
                        return {"png_bytes": buf.getvalue(), "stats": None}

                    # Растяжение гистограммы (2%-98%) для лучшего контраста
                    vmin, vmax = np.percentile(valid, [2, 98])
                    if vmax <= vmin: vmax = vmin + 1e-6
                    
                    true_min, true_max = float(np.min(valid)), float(np.max(valid))

                    # Нормализация 0..255
                    norm = np.clip((data - vmin) / (vmax - vmin), 0.0, 1.0)
                    u8 = (norm * 255).astype("uint8")
                    
                    # Покраска через LUT
                    lut = GisService._create_lut(ramp_name)
                    rgb = lut[u8]
                    
                    alpha = (~mask) * 255
                    rgba = np.dstack((rgb, alpha.astype(np.uint8)))
                    
                    img = Image.fromarray(rgba, mode="RGBA")
                    buf = io.BytesIO()
                    img.save(buf, format="PNG")
                    
                    return {
                        "png_bytes": buf.getvalue(),
                        "stats": {
                            "min": round(vmin, 2), # Значения для легенды
                            "max": round(vmax, 2), 
                            "ramp": ramp_name,
                            "colors": COLOR_RAMPS.get(ramp_name, COLOR_RAMPS["default"])
                        }
                    }
                else:
                    raise ValueError(f"Unsupported band count: {ds.count}")

    @staticmethod
    @lru_cache(maxsize=64)
    def render_raster_png(schema: str, table: str, rast_col: str):
        # Получаем настройки из конфига
        meta = RASTER_METADATA.get((schema, table), {})
        ramp_name = meta.get("ramp", "default")
        unit = meta.get("unit", "")

        res = 0.00005 
        query = sql.SQL("""
        WITH tiles AS (
            SELECT ST_SnapToGrid(ST_Resample(ST_Transform({rast_col}, 4326), {res}, {res}), {res}, {res}) AS rast
            FROM {schema}.{table} WHERE {rast_col} IS NOT NULL
        ), u AS ( SELECT ST_Union(rast) AS rast FROM tiles ),
           env AS ( SELECT ST_Extent(ST_Envelope(rast)) AS extent FROM tiles )
        SELECT ST_AsGDALRaster(u.rast, 'GTiff') AS tiff,
               ST_XMin(env.extent), ST_YMin(env.extent), ST_XMax(env.extent), ST_YMax(env.extent)
        FROM u, env;
        """).format(
            schema=sql.Identifier(schema), table=sql.Identifier(table),
            rast_col=sql.Identifier(rast_col), res=sql.Literal(res)
        )
        with GisService.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query)
                row = cur.fetchone()
                if not row or not row[0]: return None
                tiff_bytes, xmin, ymin, xmax, ymax = row
        
        result = GisService._process_raster_data(bytes(tiff_bytes), ramp_name)
        
        if result["stats"]:
            result["stats"]["unit"] = unit
            
        b64 = base64.b64encode(result["png_bytes"]).decode("ascii")
        return {
            "data_url": "data:image/png;base64," + b64, 
            "bounds": [[float(ymin), float(xmin)], [float(ymax), float(xmax)]],
            "stats": result["stats"]
        }

    # Метод vector_geojson оставляем без изменений (он уже есть в коде)
    @staticmethod
    def vector_geojson(schema, table, geom_col, limit, simplify_tol):
        query = sql.SQL("""
        WITH src AS ( SELECT * FROM {schema}.{table} WHERE {geom} IS NOT NULL LIMIT {limit} )
        SELECT json_build_object(
          'type','FeatureCollection',
          'features', COALESCE(json_agg(
            json_build_object(
              'type','Feature',
              'geometry', ST_AsGeoJSON(ST_SimplifyPreserveTopology(ST_Transform(
                      ST_SetSRID({geom}, COALESCE(NULLIF(ST_SRID({geom}),0), 4326)), 4326), {tol}))::json,
              'properties', to_jsonb(src) - {geom_literal}
            )
          ), '[]'::json)
        ) FROM src;
        """).format(
            schema=sql.Identifier(schema), table=sql.Identifier(table),
            geom=sql.Identifier(geom_col), limit=sql.Literal(int(limit)),
            tol=sql.Literal(float(simplify_tol)), geom_literal=sql.Literal(geom_col)
        )
        with GisService.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query)
                result = cur.fetchone()
                gj = result[0] if result else None
                if isinstance(gj, str): gj = json.loads(gj)
                return gj

# Инициализация (загружаем слои с новыми именами)
try:
    RASTER_LAYERS = GisService.list_rasters()
    VECTOR_LAYERS = GisService.list_vectors()
    RASTER_BY_NAME = {(r["schema"], r["name"]): r for r in RASTER_LAYERS}
    VECTOR_BY_NAME = {(v["schema"], v["name"]): v for v in VECTOR_LAYERS}
except Exception as e:
    logger.warning(f"Could not initialize GIS layers list on startup: {e}")
    RASTER_LAYERS = []
    VECTOR_LAYERS = []
    RASTER_BY_NAME = {}
    VECTOR_BY_NAME = {}