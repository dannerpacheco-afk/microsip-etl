-- Sell-out diario de los artículos cuyo proveedor predeterminado es {proveedor_id}.
-- Sin costo, sin cliente, sin precio unitario.
CREATE OR REPLACE VIEW `{project}.{dataset_proveedores}.sellout_{proveedor_id}` AS
WITH articulos AS (
  SELECT DISTINCT ARTICULO_ID
  FROM `{project}.{dataset}.dim_articulo_proveedor`
  WHERE PROVEEDOR_ID = {proveedor_id} AND ES_PROV_PREDET
)
SELECT
  v.FECHA,
  DATE_TRUNC(v.FECHA, MONTH) AS MES,
  v.ARTICULO_ID,
  a.NOMBRE AS ARTICULO,
  l.NOMBRE AS LINEA,
  al.NOMBRE AS ALMACEN,
  SUM(v.UNIDADES) AS UNIDADES,
  SUM(v.IMPORTE_NETO) AS IMPORTE_NETO
FROM `{project}.{dataset}.fact_ventas_articulo` v
JOIN articulos p ON p.ARTICULO_ID = v.ARTICULO_ID
LEFT JOIN `{project}.{dataset}.dim_articulos` a ON a.ARTICULO_ID = v.ARTICULO_ID
LEFT JOIN `{project}.{dataset}.dim_lineas` l ON l.LINEA_ARTICULO_ID = a.LINEA_ARTICULO_ID
LEFT JOIN `{project}.{dataset}.dim_almacenes` al ON al.ALMACEN_ID = v.ALMACEN_ID
GROUP BY 1, 2, 3, 4, 5, 6;
