-- Sell-in: lo que le compramos al proveedor {proveedor_id} (compras netas de devoluciones).
CREATE OR REPLACE VIEW `{project}.{dataset_proveedores}.sellin_{proveedor_id}` AS
SELECT
  c.FECHA,
  DATE_TRUNC(c.FECHA, MONTH) AS MES,
  c.ARTICULO_ID,
  a.NOMBRE AS ARTICULO,
  l.NOMBRE AS LINEA,
  al.NOMBRE AS ALMACEN,
  SUM(IF(c.TIPO_DOCTO = 'C', c.UNIDADES, -c.UNIDADES)) AS UNIDADES,
  SUM(IF(c.TIPO_DOCTO = 'C', c.PRECIO_TOTAL_NETO, -c.PRECIO_TOTAL_NETO)) AS IMPORTE_NETO,
  COUNT(DISTINCT IF(c.TIPO_DOCTO = 'C', c.DOCTO_CM_ID, NULL)) AS COMPRAS
FROM `{project}.{dataset}.fact_compras_partidas` c
LEFT JOIN `{project}.{dataset}.dim_articulos` a ON a.ARTICULO_ID = c.ARTICULO_ID
LEFT JOIN `{project}.{dataset}.dim_lineas` l ON l.LINEA_ARTICULO_ID = a.LINEA_ARTICULO_ID
LEFT JOIN `{project}.{dataset}.dim_almacenes` al ON al.ALMACEN_ID = c.ALMACEN_ID
WHERE c.PROVEEDOR_ID = {proveedor_id} AND c.ESTATUS <> 'C'
GROUP BY 1, 2, 3, 4, 5, 6;
