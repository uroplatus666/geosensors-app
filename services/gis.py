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

# Импортируем конфиг для получения параметров БД
import config

logger = logging.getLogger("app.gis")


class GisService:
    """Сервис для инкапсуляции работы с GIS базой данных."""

    SAFE_VECTOR_WHITELIST = {"boundary_campus", "buildings", "roads"}
    DEFAULT_VECTOR_LIMIT = 20000
    DEFAULT_SIMPLIFY_TOLERANCE = 0.0001

    @staticmethod
    def get_connection():
        """Отдельное подключение для GIS БД."""
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
        """Получение списка доступных растровых слоев."""
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
                    if rows: return rows

                    # Fallback: ищем таблицы в схеме rasters, если колонки не найдены явно
                    cur.execute("""
                        SELECT table_schema AS schema, table_name AS name, 'rast' AS rast_col
                        FROM information_schema.tables
                        WHERE table_schema='rasters' AND table_type='BASE TABLE'
                        ORDER BY table_name;
                    """)
                    return cur.fetchall()
        except Exception as e:
            logger.error(f"GIS DB Connection failed (rasters): {e}")
            return []

    @staticmethod
    def list_vectors():
        """Получение списка доступных векторных слоев."""
        try:
            with GisService.get_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    # Проверяем наличие PostGIS метаданных
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

                    # Fallback: ищем через information_schema
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
    def _auto_png_from_tiff(tiff_bytes: bytes) -> bytes:
        """Конвертация GeoTIFF в PNG с базовой нормализацией цвета."""
        with MemoryFile(tiff_bytes) as mem:
            with mem.open() as ds:
                bands = ds.read(masked=True)
                is_uint8 = ds.dtypes[0] == 'uint8'

                if ds.count == 1:
                    arr = bands[0]
                    data = np.ma.getdata(arr).astype(float)
                    mask = np.ma.getmask(arr)
                    valid = data[~mask]
                    if valid.size == 0:
                        img = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
                        buf = io.BytesIO();
                        img.save(buf, format="PNG");
                        return buf.getvalue()

                    lo, hi = (np.percentile(valid, [2, 98]) if valid.size >= 10 else (float(np.min(valid)),
                                                                                      float(np.max(valid))))
                    if hi <= lo: hi = lo + 1e-6
                    norm = np.clip((data - lo) / (hi - lo), 0.0, 1.0)
                    u8 = (norm * 255).astype("uint8")

                    # Simple Viridis-like LUT
                    lut_src = [(68, 1, 84), (59, 82, 139), (33, 145, 140), (94, 201, 97), (253, 231, 37)]
                    lut = np.zeros((256, 3), dtype=np.uint8)
                    for i in range(256):
                        t = i / 255.0;
                        p = t * 4.0
                        i0 = int(np.floor(p));
                        i1 = min(i0 + 1, 4);
                        a = p - i0
                        c0 = np.array(lut_src[i0], dtype=float);
                        c1 = np.array(lut_src[i1], dtype=float)
                        lut[i] = ((1 - a) * c0 + a * c1).astype(np.uint8)

                    rgb = lut[u8]
                    alpha = (~mask) * 255
                    rgba = np.dstack((rgb, alpha.astype(np.uint8)))
                elif ds.count >= 3:
                    if ds.count == 3:
                        rgb = np.ma.stack(bands, axis=-1)
                        mask = np.any(bands.mask, axis=0)
                        alpha = (~mask) * 255
                        rgba_data = np.dstack((np.ma.getdata(rgb), alpha))
                    else:
                        rgba = np.ma.stack(bands, axis=-1)
                        rgba_data = np.ma.getdata(rgba)

                    if not is_uint8:
                        rgba_data = rgba_data.astype(float)
                        for i in range(3):
                            ch = rgba_data[..., i]
                            mn, mx = ch.min(), ch.max()
                            if mx > mn: ch = (ch - mn) / (mx - mn) * 255
                            rgba_data[..., i] = ch
                        if ds.count == 4:
                            alpha_ch = rgba_data[..., 3]
                            if alpha_ch.max() <= 1.0: alpha_ch *= 255
                            rgba_data[..., 3] = alpha_ch
                    rgba = rgba_data.astype(np.uint8)
                else:
                    raise ValueError(f"Unsupported band count: {ds.count}")

                img = Image.fromarray(rgba, mode="RGBA")
                buf = io.BytesIO();
                img.save(buf, format="PNG");
                return buf.getvalue()

    @staticmethod
    @lru_cache(maxsize=64)
    def render_raster_png(schema: str, table: str, rast_col: str):
        """Рендеринг растра в PNG (Base64) для отображения на карте."""
        # Changed resolution from 0.00001 (~1m) to 0.00005 (~5m) for performance
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
            schema=sql.Identifier(schema),
            table=sql.Identifier(table),
            rast_col=sql.Identifier(rast_col),
            res=sql.Literal(res)
        )

        with GisService.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query)
                row = cur.fetchone()
                if not row or not row[0]: return None
                tiff_bytes, xmin, ymin, xmax, ymax = row

        png = GisService._auto_png_from_tiff(bytes(tiff_bytes))
        b64 = base64.b64encode(png).decode("ascii")
        return {
            "data_url": "data:image/png;base64," + b64,
            "bounds": [[float(ymin), float(xmin)], [float(ymax), float(xmax)]]
        }

    @staticmethod
    def vector_geojson(schema, table, geom_col, limit, simplify_tol):
        """Получение GeoJSON для векторного слоя."""
        query = sql.SQL("""
        WITH src AS (
          SELECT * FROM {schema}.{table} WHERE {geom} IS NOT NULL LIMIT {limit}
        )
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
            schema=sql.Identifier(schema),
            table=sql.Identifier(table),
            geom=sql.Identifier(geom_col),
            limit=sql.Literal(int(limit)),
            tol=sql.Literal(float(simplify_tol)),
            geom_literal=sql.Literal(geom_col)
        )

        with GisService.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query)
                result = cur.fetchone()
                gj = result[0] if result else None

                if isinstance(gj, str):
                    gj = json.loads(gj)
                return gj


# Инициализация глобальных списков слоев при старте (можно вызывать лениво, если нужно ускорить старт)
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