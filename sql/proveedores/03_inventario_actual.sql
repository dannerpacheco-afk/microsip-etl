-- Existencia actual (piezas) de los artículos del proveedor {proveedor_id}. Sin costo.
CREATE OR REPLACE VIEW `{project}.{dataset_proveedores}.inventario_actual_{proveedor_id}` AS
WITH articulos AS (
  SELECT DISTINCT ARTICULO_ID
  FROM `{project}.{dataset}.dim_articulo_proveedor`
  WHERE PROVEEDOR_ID = {proveedor_id} AND ES_PROV_PREDET
)
SELECT
  i.FECHA_SNAPSHOT,
  i.ARTICULO_ID,
  i.ARTICULO,
  i.LINEA,
  i.ALMACEN,
  i.EXISTENCIA,
  i.UNIDADES_90D,
  i.DIAS_INVENTARIO,
  i.ULTIMA_VENTA
FROM `{project}.{dataset}.v_inventario_actual` i
JOIN articulos p ON p.ARTICULO_ID = i.ARTICULO_ID;
