-- Existencia al cierre de mes (piezas) y venta del mes de los artículos del proveedor {proveedor_id}.
CREATE OR REPLACE VIEW `{project}.{dataset_proveedores}.inventario_mensual_{proveedor_id}` AS
WITH articulos AS (
  SELECT DISTINCT ARTICULO_ID
  FROM `{project}.{dataset}.dim_articulo_proveedor`
  WHERE PROVEEDOR_ID = {proveedor_id} AND ES_PROV_PREDET
)
SELECT
  m.PERIODO,
  m.ARTICULO_ID,
  m.ARTICULO,
  m.LINEA,
  m.ALMACEN,
  m.EXISTENCIA_FIN_MES,
  m.UNIDADES_VENDIDAS,
  m.ROTACION_MES
FROM `{project}.{dataset}.v_inventario_mensual` m
JOIN articulos p ON p.ARTICULO_ID = m.ARTICULO_ID;
