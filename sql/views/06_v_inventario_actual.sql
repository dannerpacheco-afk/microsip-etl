-- Último snapshot de existencias con valor a costo, venta diaria promedio (90 d)
-- y días de inventario por artículo/almacén.
CREATE OR REPLACE VIEW `{project}.{dataset}.v_inventario_actual` AS
WITH ultimo AS (
  SELECT MAX(_snapshot_date) AS fecha
  FROM `{project}.{dataset}.inventario_existencias`
),
venta_90 AS (
  SELECT ARTICULO_ID, ALMACEN_ID, SUM(UNIDADES) AS UNIDADES_90D
  FROM `{project}.{dataset}.fact_ventas_articulo`
  WHERE FECHA >= DATE_SUB(CURRENT_DATE(), INTERVAL 90 DAY)
  GROUP BY 1, 2
),
ultima_venta AS (
  SELECT ARTICULO_ID, ALMACEN_ID, MAX(IF(TIPO_DOCTO = 'F', FECHA, NULL)) AS ULTIMA_VENTA
  FROM `{project}.{dataset}.fact_ventas_articulo`
  GROUP BY 1, 2
),
ultima_compra AS (
  SELECT ARTICULO_ID, MAX(FECHA) AS ULTIMA_COMPRA
  FROM `{project}.{dataset}.fact_compras_partidas`
  WHERE TIPO_DOCTO = 'C' AND ESTATUS <> 'C'
  GROUP BY 1
)
SELECT
  e.EMPRESA,
  e._snapshot_date AS FECHA_SNAPSHOT,
  e.ARTICULO_ID,
  a.NOMBRE AS ARTICULO,
  a.ESTATUS AS ARTICULO_ESTATUS,
  a.LINEA_ARTICULO_ID,
  l.NOMBRE AS LINEA,
  e.ALMACEN_ID,
  al.NOMBRE AS ALMACEN,
  e.EXISTENCIA,
  e.COSTO AS VALOR_COSTO,
  SAFE_DIVIDE(e.COSTO, NULLIF(e.EXISTENCIA, 0)) AS COSTO_UNITARIO_PROM,
  COALESCE(v.UNIDADES_90D, 0) AS UNIDADES_90D,
  SAFE_DIVIDE(COALESCE(v.UNIDADES_90D, 0), 90) AS VENTA_DIARIA_PROM,
  SAFE_DIVIDE(e.EXISTENCIA, NULLIF(SAFE_DIVIDE(COALESCE(v.UNIDADES_90D, 0), 90), 0)) AS DIAS_INVENTARIO,
  uv.ULTIMA_VENTA,
  DATE_DIFF(CURRENT_DATE(), uv.ULTIMA_VENTA, DAY) AS DIAS_SIN_VENTA,
  uc.ULTIMA_COMPRA
FROM `{project}.{dataset}.inventario_existencias` e
JOIN ultimo ON e._snapshot_date = ultimo.fecha
LEFT JOIN `{project}.{dataset}.dim_articulos` a ON a.ARTICULO_ID = e.ARTICULO_ID
LEFT JOIN `{project}.{dataset}.dim_lineas` l ON l.LINEA_ARTICULO_ID = a.LINEA_ARTICULO_ID
LEFT JOIN `{project}.{dataset}.dim_almacenes` al ON al.ALMACEN_ID = e.ALMACEN_ID
LEFT JOIN venta_90 v ON v.ARTICULO_ID = e.ARTICULO_ID AND v.ALMACEN_ID = e.ALMACEN_ID
LEFT JOIN ultima_venta uv ON uv.ARTICULO_ID = e.ARTICULO_ID AND uv.ALMACEN_ID = e.ALMACEN_ID
LEFT JOIN ultima_compra uc ON uc.ARTICULO_ID = e.ARTICULO_ID;
