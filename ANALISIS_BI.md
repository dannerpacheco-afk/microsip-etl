# Análisis de negocio — Qué datos subir a Looker Studio para vender más

> Basado en datos reales de Microsip (empresa por defecto), consultados vía MCP el 2026-07.

## Perfil del negocio

Distribuidora de abarrotes / consumo, venta de **mayoreo** (no punto de venta).

| Métrica | Valor |
|---|---|
| Venta neta mensual | ~$50M MXN |
| Facturas / mes | ~3,700 (~44k/año) |
| Clientes activos (12 meses) | 1,380 |
| Vendedores activos | 15+ (rango $8M – $103M / 12mo) |
| Tickets punto de venta (PV) | **0** |
| Historia en DB | 18 años (716k facturas) |

Ventas concentradas por **línea = marca**: Nestlé ($61M), Azúcar ($46M), Unilever ($36M), Colgate ($32M), Kimberly Clark ($29M), Peñafiel, Harinas de Chihuahua, P&G, La Costeña, Coca Cola...

## Palancas para vender más (qué preguntas responde el BI)

1. **Recompra / clientes en riesgo** — clientes que compraban y dejaron de comprar. Mayor palanca en mayoreo: retener > adquirir. (API ya expone `/clientes/sin-compra/{vendedor_id}`.)
2. **Desempeño por vendedor** — ranking, tendencia, brecha vs meta. Rango 12x entre top y bottom → coaching.
3. **Mezcla de producto por cliente** — qué marcas compra cada cliente y cuáles NO → cross-sell dirigido.
4. **Cobertura de marca** — avance por línea/marca vs periodo anterior (API: `/ventas/avance-marca`).
5. **Disponibilidad** — no perder venta por faltante; detectar stock muerto (capital parado).

## Qué cargar a BigQuery (mínimo, para no generar costos)

Costo BQ = almacenamiento (10GB gratis) + query escaneado (1TB/mes gratis). Reglas:

### Cortar de tajo
- **`pv_tickets`** — 0 filas. Eliminar del pipeline.
- **Historia > 24 meses** — filtrar `fecha_inicio` a 24 meses. Corta 716k → ~90k facturas.
- **pedidos / cotizaciones / remisiones** — opcionales. Para "vender más" bastan **facturas + devoluciones** (venta neta real). Dejar fuera en v1; sumar si se necesita pipeline de pedidos.

### Cargar (v1)
| Tabla BQ | Fuente | Estrategia | Notas de costo |
|---|---|---|---|
| `ventas_documentos` | facturas + devoluciones | incremental por fecha | Partición DAY por FECHA, cluster VENDEDOR_ID, CLIENTE_ID. Solo 24mo. |
| `ventas_detalle` | líneas (DET) 24mo | incremental | **Tabla más pesada.** Partición DAY, cluster ARTICULO_ID. Necesaria para análisis por producto/marca. Ver gap abajo. |
| `dim_clientes` | /clientes | full refresh | Chica (~8.5k). |
| `dim_articulos` | /articulos | full refresh | ~12k. |
| `dim_vendedores` | /catalogos/vendedores | full refresh | Trivial. |
| `dim_lineas` | /catalogos/lineas | full refresh | Trivial. |
| `dim_almacenes` | /catalogos/almacenes | full refresh | Trivial. |
| `inventario_existencias` | /inventarios/existencias | snapshot | Cambiar a **semanal**, no diario — reduce crecimiento. |

### Optimización Looker
- Data sources apuntan a tablas particionadas; filtros de fecha SIEMPRE activos.
- Evitar `SELECT *` en Looker; elegir columnas.
- Considerar tablas pre-agregadas (venta_mensual_x_vendedor, venta_x_linea) si el escaneo crece — Looker consulta agregado chico, no la fact table.
- BI Engine (1GB gratis) para dashboards frecuentes.

## Gap detectado (requiere decisión)

**Detalle de línea (`ventas_detalle`) es la palanca #3 y #4, pero la API solo expone líneas vía endpoint por-documento (`/ventas/facturas/{id}`)** → N+1: 44k llamadas/año. Inviable para carga diaria.

Opciones:
1. Agregar endpoint bulk en la API: `/ventas/detalle?fecha_inicio=&fecha_fin=` que pagine líneas con JOIN a header (rango de fecha). **Recomendado.**
2. Usar los endpoints KPI ya existentes (`/ventas/kpis`, `/avance-marca`, `/resumen-equipo`) como fuente agregada — barato, pero menos flexible en Looker.
3. Diferir detalle: v1 solo header (vendedor, cliente, total). Cubre palancas #1, #2, #5. Producto/marca (#3, #4) queda para v2.

**Margen real** (costo por línea) no está en DET; vive en `SALDOS_IN` (ENTRADAS/SALIDAS_COSTO) y tablas de costo. Análisis de rentabilidad = v2.

## Recomendación de arranque

v1 barato y útil ya: **facturas + devoluciones (header, 24mo) + dims + existencias semanal**, sin PV, sin pedidos/cotizaciones. Dashboards: ranking vendedor, clientes sin compra, venta mensual, venta por cliente.

Decidir gap de detalle antes de v2 (producto/marca).
