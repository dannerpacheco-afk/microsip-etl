-- Ventas netas por cliente, vendedor y mes. Ticket promedio = importe / facturas.
-- ZONA / FORMATO salen del tipo de cliente (TIPOS_CLIENTES se usa como catálogo
-- de rutas) mapeado con dim_formato_venta (config/formatos_venta.csv). La
-- regla se repite aquí porque esta vista se crea antes que v_tipos_clientes_formato.
CREATE OR REPLACE VIEW `{project}.{dataset}.v_ventas_cliente_mes` AS
WITH tipo_formato AS (
  SELECT
    t.TIPO_CLIENTE_ID,
    TRIM(t.NOMBRE) AS ZONA,
    COALESCE(f.FORMATO, 'Otros') AS FORMATO
  FROM `{project}.{dataset}.dim_tipos_clientes` t
  LEFT JOIN `{project}.{dataset}.dim_formato_venta` f
    ON REGEXP_CONTAINS(UPPER(TRIM(t.NOMBRE)), UPPER(f.PATRON))
  QUALIFY ROW_NUMBER() OVER (PARTITION BY t.TIPO_CLIENTE_ID ORDER BY f.ORDEN NULLS LAST) = 1
)
SELECT
  v.EMPRESA,
  DATE_TRUNC(v.FECHA, MONTH) AS MES,
  v.CLIENTE_ID,
  c.NOMBRE AS CLIENTE,
  c.ESTATUS AS CLIENTE_ESTATUS,
  c.TIPO_CLIENTE_ID,
  tf.ZONA,
  COALESCE(tf.FORMATO, 'Otros') AS FORMATO,
  v.VENDEDOR_ID,
  ve.NOMBRE AS VENDEDOR,
  SUM(v.IMPORTE_NETO) AS IMPORTE_NETO,
  SUM(v.COSTO) AS COSTO,
  SUM(v.UTILIDAD) AS UTILIDAD,
  SAFE_DIVIDE(SUM(v.UTILIDAD), SUM(v.IMPORTE_NETO)) AS MARGEN_PCT,
  COUNT(DISTINCT IF(v.TIPO_DOCTO = 'F', v.DOCTO_VE_ID, NULL)) AS FACTURAS,
  COUNT(DISTINCT IF(v.TIPO_DOCTO = 'D', v.DOCTO_VE_ID, NULL)) AS DEVOLUCIONES,
  COUNT(DISTINCT v.ARTICULO_ID) AS ARTICULOS_DISTINTOS,
  SAFE_DIVIDE(
    SUM(IF(v.TIPO_DOCTO = 'F', v.IMPORTE_NETO, 0)),
    COUNT(DISTINCT IF(v.TIPO_DOCTO = 'F', v.DOCTO_VE_ID, NULL))
  ) AS TICKET_PROMEDIO
FROM `{project}.{dataset}.fact_ventas_articulo` v
LEFT JOIN `{project}.{dataset}.dim_clientes` c ON c.CLIENTE_ID = v.CLIENTE_ID
LEFT JOIN tipo_formato tf ON tf.TIPO_CLIENTE_ID = c.TIPO_CLIENTE_ID
LEFT JOIN `{project}.{dataset}.dim_vendedores` ve ON ve.VENDEDOR_ID = v.VENDEDOR_ID
GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9, 10;
