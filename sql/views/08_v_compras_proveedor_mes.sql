-- Sell-in: compras netas (C − D, sin canceladas) por proveedor, artículo y mes.
CREATE OR REPLACE VIEW `{project}.{dataset}.v_compras_proveedor_mes` AS
SELECT
  c.EMPRESA,
  DATE_TRUNC(c.FECHA, MONTH) AS MES,
  c.PROVEEDOR_ID,
  p.NOMBRE AS PROVEEDOR,
  c.ARTICULO_ID,
  a.NOMBRE AS ARTICULO,
  a.LINEA_ARTICULO_ID,
  l.NOMBRE AS LINEA,
  c.ALMACEN_ID,
  al.NOMBRE AS ALMACEN,
  SUM(IF(c.TIPO_DOCTO = 'C', c.UNIDADES, -c.UNIDADES)) AS UNIDADES,
  SUM(IF(c.TIPO_DOCTO = 'C', c.PRECIO_TOTAL_NETO, -c.PRECIO_TOTAL_NETO)) AS IMPORTE_NETO,
  SAFE_DIVIDE(
    SUM(IF(c.TIPO_DOCTO = 'C', c.PRECIO_TOTAL_NETO, 0)),
    SUM(IF(c.TIPO_DOCTO = 'C', c.UNIDADES, 0))
  ) AS COSTO_UNITARIO_PROM,
  COUNT(DISTINCT IF(c.TIPO_DOCTO = 'C', c.DOCTO_CM_ID, NULL)) AS COMPRAS,
  COUNT(DISTINCT IF(c.TIPO_DOCTO = 'D', c.DOCTO_CM_ID, NULL)) AS DEVOLUCIONES
FROM `{project}.{dataset}.fact_compras_partidas` c
LEFT JOIN `{project}.{dataset}.dim_proveedores` p ON p.PROVEEDOR_ID = c.PROVEEDOR_ID
LEFT JOIN `{project}.{dataset}.dim_articulos` a ON a.ARTICULO_ID = c.ARTICULO_ID
LEFT JOIN `{project}.{dataset}.dim_lineas` l ON l.LINEA_ARTICULO_ID = a.LINEA_ARTICULO_ID
LEFT JOIN `{project}.{dataset}.dim_almacenes` al ON al.ALMACEN_ID = c.ALMACEN_ID
WHERE c.ESTATUS <> 'C'
GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9, 10;
