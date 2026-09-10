# Looker Studio: reportes sobre BigQuery

Proyecto: `lookerstudio-microsip`. Dataset interno: `microsip`. Dataset de
proveedores: `microsip_proveedores`. Looker se conecta **solo a vistas**.

## Reporte interno (3 páginas + estado)

Crear el reporte con las fuentes de datos ya ligadas usando la Linking API
(abre Looker Studio con la vista como fuente; guardar con "Crear informe"):

| Página | Vista | Liga |
|---|---|---|
| Ventas | `v_ventas_diarias_articulo` | https://lookerstudio.google.com/reporting/create?ds.connector=bigQuery&ds.type=TABLE&ds.projectId=lookerstudio-microsip&ds.datasetId=microsip&ds.tableId=v_ventas_diarias_articulo |
| Clientes | `v_ventas_cliente_mes`, `v_clientes_resumen` | https://lookerstudio.google.com/reporting/create?ds.connector=bigQuery&ds.type=TABLE&ds.projectId=lookerstudio-microsip&ds.datasetId=microsip&ds.tableId=v_clientes_resumen |
| Inventario | `v_inventario_actual`, `v_inventario_mensual`, `v_articulos_sin_movimiento` | https://lookerstudio.google.com/reporting/create?ds.connector=bigQuery&ds.type=TABLE&ds.projectId=lookerstudio-microsip&ds.datasetId=microsip&ds.tableId=v_inventario_actual |
| Compras | `v_compras_proveedor_mes`, `v_ultimo_costo_articulo` | https://lookerstudio.google.com/reporting/create?ds.connector=bigQuery&ds.type=TABLE&ds.projectId=lookerstudio-microsip&ds.datasetId=microsip&ds.tableId=v_compras_proveedor_mes |
| Estado ETL | `v_etl_estado` | https://lookerstudio.google.com/reporting/create?ds.connector=bigQuery&ds.type=TABLE&ds.projectId=lookerstudio-microsip&ds.datasetId=microsip&ds.tableId=v_etl_estado |

Dentro del reporte, "Agregar datos" para sumar las demás vistas de cada página.

### Contenido sugerido

**Ventas**
- Scorecards: importe neto, utilidad, margen %, facturas (rango de fechas).
- Serie mensual importe vs utilidad (`MES`).
- Tabla top artículos por importe con `MARGEN_PCT` y `UNIDADES_DEVUELTAS`.
- Filtros: `LINEA`, `ALMACEN`, `ARTICULO_ESTATUS`.
- Comparación de periodo: activar "Comparar con periodo anterior" en el rango de fechas.

**Clientes**
- Tabla `v_clientes_resumen` ordenada por `IMPORTE_12M`; color por `SEGMENTO_RECENCIA`.
- Gráfica de barras: clientes por segmento.
- Filtro `VENDEDOR`.
- Serie `v_ventas_cliente_mes` por vendedor.

**Inventario**
- Scorecards: valor a costo total, artículos con existencia, sin venta > 180 d.
- Tabla `v_inventario_actual` con `DIAS_INVENTARIO` (mapa de calor).
- Serie `v_inventario_mensual`: `EXISTENCIA_FIN_MES` y `UNIDADES_VENDIDAS` por línea.
- Tabla `v_articulos_sin_movimiento` por `ANTIGUEDAD_SIN_VENTA`.

**Compras**
- Serie mensual sell-in por proveedor (`IMPORTE_NETO`).
- Tabla proveedor → artículo con `COSTO_UNITARIO_PROM` y `COMPRAS`.
- Tabla `v_ultimo_costo_articulo` (último costo, proveedor predeterminado, lead time).
- Cruce sell-in vs sell-out: combinar datos (blend) `v_compras_proveedor_mes` y
  `v_ventas_diarias_articulo` por `ARTICULO_ID` + `MES`.

**Estado ETL**
- Tabla `v_etl_estado`; regla: `horas_desde_corrida > 30` en rojo.

## Reporte por proveedor

1. Agregar el `PROVEEDOR_ID` a `PROVEEDORES_BI` en `.env` y correr
   `python main.py proveedores-views` (crea `sellout_<id>`, `sellin_<id>`,
   `inventario_actual_<id>`, `inventario_mensual_<id>` en
   `microsip_proveedores` y las autoriza sobre `microsip`).
2. Crear la plantilla una sola vez con la liga:
   `https://lookerstudio.google.com/reporting/create?ds.connector=bigQuery&ds.type=TABLE&ds.projectId=lookerstudio-microsip&ds.datasetId=microsip_proveedores&ds.tableId=sellout_<id>`
   Páginas: Sell-out (serie mensual + tabla por artículo/almacén), Sell-in,
   Inventario (actual y cierre de mes).
3. Para otro proveedor: "Hacer una copia" del reporte y cambiar cada fuente
   a la vista del nuevo id.
4. Compartir: "Compartir" → agregar el correo Google del proveedor como
   **Lector**. No usar "cualquier persona con el vínculo". Las credenciales
   de la fuente deben quedar en modo **Propietario** para que el proveedor
   no necesite acceso a BigQuery.

Decisión pendiente: `sellout_<id>` expone `IMPORTE_NETO` (importe de venta).
Si solo se quiere compartir piezas, quitar esa columna en
`sql/proveedores/01_sellout.sql` y volver a correr `proveedores-views`.
