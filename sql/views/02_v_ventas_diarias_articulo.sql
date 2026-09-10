-- Sell-out neto (facturas − devoluciones) por día, artículo y almacén, con costo y margen.
CREATE OR REPLACE VIEW `{project}.{dataset}.v_ventas_diarias_articulo` AS
SELECT
  v.EMPRESA,
  v.FECHA,
  DATE_TRUNC(v.FECHA, MONTH) AS MES,
  v.ARTICULO_ID,
  a.NOMBRE AS ARTICULO,
  a.ESTATUS AS ARTICULO_ESTATUS,
  a.LINEA_ARTICULO_ID,
  l.NOMBRE AS LINEA,
  l.GRUPO_LINEA_ID,
  v.ALMACEN_ID,
  al.NOMBRE AS ALMACEN,
  SUM(v.UNIDADES) AS UNIDADES,
  SUM(v.IMPORTE_NETO) AS IMPORTE_NETO,
  SUM(v.COSTO) AS COSTO,
  SUM(v.UTILIDAD) AS UTILIDAD,
  SAFE_DIVIDE(SUM(v.UTILIDAD), SUM(v.IMPORTE_NETO)) AS MARGEN_PCT,
  COUNT(DISTINCT IF(v.TIPO_DOCTO = 'F', v.DOCTO_VE_ID, NULL)) AS FACTURAS,
  COUNT(DISTINCT IF(v.TIPO_DOCTO = 'D', v.DOCTO_VE_ID, NULL)) AS DEVOLUCIONES,
  SUM(IF(v.TIPO_DOCTO = 'D', -v.UNIDADES, 0)) AS UNIDADES_DEVUELTAS
FROM `{project}.{dataset}.fact_ventas_articulo` v
LEFT JOIN `{project}.{dataset}.dim_articulos` a ON a.ARTICULO_ID = v.ARTICULO_ID
LEFT JOIN `{project}.{dataset}.dim_lineas` l ON l.LINEA_ARTICULO_ID = a.LINEA_ARTICULO_ID
LEFT JOIN `{project}.{dataset}.dim_almacenes` al ON al.ALMACEN_ID = v.ALMACEN_ID
GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11;
