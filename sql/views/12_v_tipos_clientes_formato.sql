-- TIPOS_CLIENTES se usa en esta empresa como catálogo de rutas / zonas de
-- venta. Cada tipo se mapea a un FORMATO de venta con las reglas regex de
-- dim_formato_venta (config/formatos_venta.csv): gana la primera coincidencia
-- por ORDEN; sin coincidencia → 'Otros' e INCLUIR = TRUE.
CREATE OR REPLACE VIEW `{project}.{dataset}.v_tipos_clientes_formato` AS
SELECT
  t.TIPO_CLIENTE_ID,
  TRIM(t.NOMBRE) AS ZONA,
  COALESCE(f.FORMATO, 'Otros') AS FORMATO,
  COALESCE(f.INCLUIR, TRUE) AS INCLUIR,
  f.ORDEN AS ORDEN_REGLA,
  f.PATRON AS PATRON_REGLA
FROM `{project}.{dataset}.dim_tipos_clientes` t
LEFT JOIN `{project}.{dataset}.dim_formato_venta` f
  ON REGEXP_CONTAINS(UPPER(TRIM(t.NOMBRE)), UPPER(f.PATRON))
QUALIFY ROW_NUMBER() OVER (PARTITION BY t.TIPO_CLIENTE_ID ORDER BY f.ORDEN NULLS LAST) = 1;
