-- Artículos con existencia y sin venta en 180 días (candidatos a liquidar / no recomprar).
CREATE OR REPLACE VIEW `{project}.{dataset}.v_articulos_sin_movimiento` AS
SELECT
  EMPRESA,
  ARTICULO_ID,
  ARTICULO,
  ARTICULO_ESTATUS,
  LINEA,
  ALMACEN_ID,
  ALMACEN,
  EXISTENCIA,
  VALOR_COSTO,
  ULTIMA_VENTA,
  DIAS_SIN_VENTA,
  ULTIMA_COMPRA,
  CASE
    WHEN ULTIMA_VENTA IS NULL THEN 'Nunca vendido (3 años)'
    WHEN DIAS_SIN_VENTA > 365 THEN 'Más de 1 año'
    WHEN DIAS_SIN_VENTA > 180 THEN '6 a 12 meses'
    ELSE 'Menos de 6 meses'
  END AS ANTIGUEDAD_SIN_VENTA
FROM `{project}.{dataset}.v_inventario_actual`
WHERE EXISTENCIA > 0
  AND (ULTIMA_VENTA IS NULL OR DIAS_SIN_VENTA > 180);
