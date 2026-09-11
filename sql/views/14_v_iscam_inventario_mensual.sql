-- Existencia y valor a costo al cierre de mes por almacén × artículo para el
-- reporte ISCAM, con clave principal, código de barras y proveedor
-- predeterminado. Base: v_inventario_mensual (PERIODO → MES).
CREATE OR REPLACE VIEW `{project}.{dataset}.v_iscam_inventario_mensual` AS
WITH proveedor_predet AS (
  SELECT ARTICULO_ID, PROVEEDOR_ID
  FROM `{project}.{dataset}.dim_articulo_proveedor`
  WHERE ES_PROV_PREDET
  QUALIFY ROW_NUMBER() OVER (PARTITION BY ARTICULO_ID ORDER BY PRIORIDAD_COMPRA, PRECIO_COMPRA_ID) = 1
)
SELECT
  i.EMPRESA,
  i.PERIODO AS MES,
  i.ALMACEN_ID,
  i.ALMACEN,
  i.ARTICULO_ID,
  k.CLAVE_PRINCIPAL,
  k.CODIGO_BARRAS,
  a.NOMBRE AS DESCRIPCION,
  a.UNIDAD_VENTA,
  p.NOMBRE AS PROVEEDOR,
  i.EXISTENCIA_FIN_MES,
  i.VALOR_COSTO_FIN_MES
FROM `{project}.{dataset}.v_inventario_mensual` i
LEFT JOIN `{project}.{dataset}.dim_articulos` a ON a.ARTICULO_ID = i.ARTICULO_ID
LEFT JOIN `{project}.{dataset}.v_articulo_claves` k ON k.ARTICULO_ID = i.ARTICULO_ID
LEFT JOIN proveedor_predet pp ON pp.ARTICULO_ID = i.ARTICULO_ID
LEFT JOIN `{project}.{dataset}.dim_proveedores` p ON p.PROVEEDOR_ID = pp.PROVEEDOR_ID;
