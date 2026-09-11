-- Ventas netas (facturas − devoluciones) por mes × formato × zona × almacén ×
-- artículo para el reporte ISCAM: piezas, importe sin/con impuestos, clave
-- principal, código de barras y proveedor predeterminado.
-- IMPUESTOS / IMPORTE_TOTAL vienen de /etl/ventas-articulo; filas cargadas
-- antes de esa columna quedan en NULL hasta re-correr el backfill.
CREATE OR REPLACE VIEW `{project}.{dataset}.v_iscam_ventas_mensual` AS
WITH proveedor_predet AS (
  SELECT ARTICULO_ID, PROVEEDOR_ID
  FROM `{project}.{dataset}.dim_articulo_proveedor`
  WHERE ES_PROV_PREDET
  QUALIFY ROW_NUMBER() OVER (PARTITION BY ARTICULO_ID ORDER BY PRIORIDAD_COMPRA, PRECIO_COMPRA_ID) = 1
)
SELECT
  v.EMPRESA,
  DATE_TRUNC(v.FECHA, MONTH) AS MES,
  COALESCE(tf.FORMATO, 'Otros') AS FORMATO,
  COALESCE(tf.INCLUIR, TRUE) AS INCLUIR,
  COALESCE(tf.ZONA, 'Sin zona') AS ZONA,
  v.ALMACEN_ID,
  al.NOMBRE AS ALMACEN,
  v.ARTICULO_ID,
  k.CLAVE_PRINCIPAL,
  k.CODIGO_BARRAS,
  a.NOMBRE AS DESCRIPCION,
  a.UNIDAD_VENTA,
  p.NOMBRE AS PROVEEDOR,
  SUM(v.UNIDADES) AS PIEZAS,
  SUM(v.IMPORTE_NETO) AS IMPORTE_SIN_IMPUESTOS,
  SUM(v.IMPUESTOS) AS IMPUESTOS,
  SUM(v.IMPORTE_TOTAL) AS IMPORTE_CON_IMPUESTOS
FROM `{project}.{dataset}.fact_ventas_articulo` v
LEFT JOIN `{project}.{dataset}.dim_clientes` c ON c.CLIENTE_ID = v.CLIENTE_ID
LEFT JOIN `{project}.{dataset}.v_tipos_clientes_formato` tf ON tf.TIPO_CLIENTE_ID = c.TIPO_CLIENTE_ID
LEFT JOIN `{project}.{dataset}.dim_articulos` a ON a.ARTICULO_ID = v.ARTICULO_ID
LEFT JOIN `{project}.{dataset}.v_articulo_claves` k ON k.ARTICULO_ID = v.ARTICULO_ID
LEFT JOIN proveedor_predet pp ON pp.ARTICULO_ID = v.ARTICULO_ID
LEFT JOIN `{project}.{dataset}.dim_proveedores` p ON p.PROVEEDOR_ID = pp.PROVEEDOR_ID
LEFT JOIN `{project}.{dataset}.dim_almacenes` al ON al.ALMACEN_ID = v.ALMACEN_ID
GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13;
