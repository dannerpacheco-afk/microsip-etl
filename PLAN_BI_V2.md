# Plan: Microsip → BigQuery → Looker Studio (v2)

Fecha: 2026-09-10. Empresa: ALMACENES PACHECO. Ventana: últimos 3 años (rolling).

## 1. Diagnóstico

### Lo que ya existe
- `microsip-etl` **ya consume la API** (`httpx`, header `X-API-Key`, paginación 500 filas) y carga a BigQuery. No hay ningún query directo a Firebird en este repo. La premisa "extraer sin queries directos" ya se cumple.
- Tablas actuales: `dim_clientes`, `dim_articulos`, `dim_almacenes`, `dim_vendedores`, `dim_lineas`, `ventas_documentos` (encabezados F/R/P/C/D), `pv_tickets`, `inventario_existencias` (snapshot diario), `_etl_sync_state`.
- Estrategias: catálogos `WRITE_TRUNCATE`; ventas MERGE por `DOCTO_VE_ID` en staging; snapshots `WRITE_APPEND`.

### Huecos que impiden el BI que quieres
| Hueco | Efecto | Dónde |
|---|---|---|
| La API no tiene ventas a nivel **partida/artículo** ni modo bulk | Sin sell-out por producto, sin nada que compartir a proveedores | `API de Microsip/routers/ventas.py` |
| Paginación offset, máximo 500 filas | 2.1M partidas en 3 años = 4,200 requests y O(n²) en Firebird | `microsip_core/utils/queries.py:161` |
| `tipo_docto` se loguea pero nunca se escribe a BQ | Clustering por `TIPO_DOCTO` no sirve, no se distingue F de D | `pipeline.py:108-117` |
| Snapshots duplican si corre 2 veces el mismo día | Existencias infladas | `pipeline.py:187-193` |
| Esquemas BQ `autodetect` | Tipos cambian entre corridas, MERGE frágil | `schemas.py:6` |
| `pv_tickets` | `DOCTOS_PV` está vacío en las 5 empresas | `schemas.py:78` |
| Sin vistas para Looker, sin dataset para proveedores | Looker pega a tablas crudas | — |
| Lookback inicial 30 días | No hay histórico | `config.py:25` |

### Volumen medido (ALMACENES PACHECO)
| Dato | Cantidad |
|---|---|
| Facturas/año | ~45–50k |
| Devoluciones/año | ~7.5k |
| Remisiones 2026 | 5,542 (antes ≤60/año) |
| Partidas F+R+D por año | ~750k → **~2.1M en 3 años** |
| Compras (recepciones) 3 años | 15.8k docs / 61k partidas |
| Artículos activos | 3,849 de 12,220 |
| Almacenes | 23 |
| Clientes / Proveedores | 8,649 / 1,705 |

## 2. Decisiones tomadas (respuestas del 2026-09-10)
1. Solo ALMACENES PACHECO en fase 1. Se agrega columna `EMPRESA` desde ahora para no migrar después.
2. Sí se agregan endpoints `/etl/*` a la API, en rama `feat/etl-endpoints`; merge después.
3. Venta = **Facturas − Devoluciones** a nivel artículo. Remisiones cuentan solo cuando se facturan.
4. GCP existe (proyecto, BigQuery, credenciales).
5. ETL corre en **Docker + cron en el servidor de la API**, corrida diaria nocturna.
6. Costos y margen sí, en dataset interno separado del de proveedores.
7. Artículo↔proveedor por `PRECIOS_COMPRA` (proveedor predeterminado).

## 3. Arquitectura objetivo

```
Firebird (prod)
   │  usuario FB_MCP_USER, GRANT SELECT only, pool read-only
   ▼
API de Microsip  ──  router /api/v1/etl/*  (nuevo, rama feat/etl-endpoints)
   │  X-API-Key dedicada (scope read, ACL empresa = ALMACENES PACHECO)
   │  X-Empresa: ALMACENES PACHECO
   ▼
microsip-etl (Docker, cron 02:30, mismo servidor)
   │  service account BQ (roles/bigquery.dataEditor + jobUser)
   ▼
BigQuery
   ├─ dataset microsip              (interno: costo, margen, clientes, CxC)
   └─ dataset microsip_proveedores  (vistas autorizadas sin costo, 1 por proveedor)
   ▼
Looker Studio
   ├─ Reporte interno (ventas, inventario, compras)
   └─ Plantilla proveedor (copia por proveedor, apunta a su vista)
```

Superficie sobre la base de producción: un usuario Firebird de solo lectura, un router de solo lectura, una API key, una conexión, de noche.

## 4. Endpoints nuevos en la API (`routers/etl.py`)

Todos usan `Depends(get_db_readonly)` (pool `FB_MCP_USER`), sin `include_total`, límite propio de 5,000 filas por página (como ya hace `inventarios/pedimentos`), keyset por ID.

| Endpoint | Fuente | Grano | Paginación | Uso |
|---|---|---|---|---|
| `GET /etl/ventas-articulo?fecha_inicio&fecha_fin` | `MARGEN_DOCTOS_PER_XD(fi, ff, 'N', 'N')` ⋈ `DOCTOS_VE` | docto × artículo × almacén | **sin paginar**, 1 request por semana (evita re-ejecutar el proc por página); tope 50k filas | sell-out neto F−D, unidades, importe, **costo**, utilidad. Mismo cálculo que el reporte de margen de Microsip |
| `GET /etl/ventas-partidas?fecha_inicio&fecha_fin&cursor` | `DOCTOS_VE_DET` ⋈ `DOCTOS_VE` (F, D) | partida | keyset `DOCTO_VE_DET_ID` | precio unitario, descuentos, posición. **Opcional**, solo si se necesita análisis de descuentos |
| `GET /etl/compras-partidas?fecha_inicio&fecha_fin&cursor` | `DOCTOS_CM_DET` ⋈ `DOCTOS_CM` (C, D) | partida | keyset `DOCTO_CM_DET_ID` | sell-in por proveedor, costo de compra, folio proveedor |
| `GET /etl/saldos-mensuales?anio&mes` | `SALDOS_IN` | artículo × almacén × mes | 1 request por mes | inventario histórico mensual (deltas) |
| `GET /etl/saldos-iniciales?hasta=YYYY-MM` | `SALDOS_IN` acumulado | artículo × almacén | 1 request | saldo de apertura para la ventana de 3 años |
| `GET /etl/articulos-proveedores?cursor` | `PRECIOS_COMPRA` | artículo × proveedor | keyset `PRECIO_COMPRA_ID` | mapa proveedor, `ES_PROV_PREDET`, `DIAS_ENTREGA_PROM`, `FECHA_PRECIO_ULT_COMPRA` |
| `GET /etl/ordenes-pendientes` | `DOCTOS_CM` 'O' con `UNIDADES_A_REC > 0` | partida | keyset | en tránsito (fase 2) |
| `GET /etl/pedidos-pendientes` | `DOCTOS_VE` 'P' con `UNIDADES_A_SURTIR > 0` | partida | keyset | backorder (fase 2) |

Se reutilizan sin cambios: `/inventarios/existencias`, `/articulos`, `/clientes`, `/proveedores`, `/catalogos/*`.

Columnas de `ventas-articulo`: `EMPRESA, DOCTO_VE_ID, TIPO_DOCTO, FOLIO, FECHA, CLIENTE_ID, VENDEDOR_ID, ALMACEN_ID, ARTICULO_ID, UNIDADES (VENTA_UNID*SIGNO), IMPORTE_NETO (VENTA_IMPORTE*SIGNO), COSTO (COSTO_IMPORTE*SIGNO), UTILIDAD, MONEDA_ID, TIPO_CAMBIO`.

Clave primaria BQ: `(DOCTO_VE_ID, ARTICULO_ID, ALMACEN_ID)`.

## 5. Cambios en `microsip-etl`

1. **Esquemas explícitos** para tablas nuevas (`schemas.py`): quitar `autodetect` en fact tables.
2. **Backfill por chunks** (`pipeline.py`): `BACKFILL_START_DATE` (default hoy − 3 años). Ventas por semana, compras por mes, saldos por mes. Reanudable: `_etl_sync_state` guarda el último chunk OK.
3. **Incremental sin depender de `ult_modif`**: cada noche recarga ventana rodante de 45 días (`DELETE WHERE FECHA >= hoy−45` + insert). Cubre cancelaciones y devoluciones tardías sin lógica de diffs.
4. **Fix `tipo_docto`**: escribirlo en cada fila de `ventas_documentos`.
5. **Snapshot idempotente**: borrar partición `_snapshot_date = hoy` antes de append.
6. **Quitar `pv_tickets`.**
7. **Columna `EMPRESA`** en todas las tablas (constante por ahora).
8. **Retención automática**: `partition_expiration_days = 1100` en fact tables y `400` en snapshots diarios. La ventana de 3 años se mantiene sola.
9. Cliente HTTP: `page_size` por tabla (5,000 para `/etl`), `X-Empresa` header, timeout 120 s en endpoints bulk.
10. Dockerfile + `docker-compose.yml` servicio `etl` + cron `30 2 * * *` en el servidor de la API. Log a stdout, `_etl_sync_state` como monitor.

Tablas BQ resultantes (dataset `microsip`):

| Tabla | Tipo | Partición | Cluster |
|---|---|---|---|
| `fact_ventas_articulo` | rolling 3 años | `FECHA` día | `ARTICULO_ID, ALMACEN_ID` |
| `fact_compras_partidas` | rolling 3 años | `FECHA` día | `PROVEEDOR_ID, ARTICULO_ID` |
| `fact_saldos_mensuales` | rolling 3 años + apertura | `PERIODO` mes | `ARTICULO_ID, ALMACEN_ID` |
| `inventario_existencias` | snapshot diario, 400 días | `_snapshot_date` | `ALMACEN_ID` |
| `ventas_documentos` | encabezados (ya existe) | `FECHA` | `TIPO_DOCTO, CLIENTE_ID` |
| `dim_articulos`, `dim_clientes`, `dim_proveedores`, `dim_articulo_proveedor`, `dim_almacenes`, `dim_vendedores`, `dim_lineas` | catálogo | — | — |

## 6. Vistas para Looker Studio

Dataset `microsip` (interno):
- `v_ventas_diarias_articulo`: fecha, artículo, línea, almacén, unidades, importe, costo, margen %.
- `v_ventas_cliente_mes`: cliente, vendedor, mes, importe, # facturas, ticket promedio, última compra.
- `v_inventario_actual`: último snapshot, existencia por artículo/almacén, valor a último costo, días de inventario (existencia / venta diaria promedio 90 d).
- `v_inventario_mensual`: `saldos_iniciales + SUM(deltas) OVER (ORDER BY periodo)` → existencia fin de mes 36 meses, rotación.
- `v_compras_proveedor_mes`: proveedor, mes, unidades, importe, # recepciones.
- `v_margen_linea_mes`.

Dataset `microsip_proveedores` (vistas autorizadas, **sin costo, sin cliente, sin precio unitario**):
- `v_sellout_prov_<PROVEEDOR_ID>`: fecha, artículo, almacén, unidades, importe neto.
- `v_sellin_prov_<PROVEEDOR_ID>`: fecha, artículo, unidades, importe.
- `v_inventario_prov_<PROVEEDOR_ID>`: snapshot actual + mensual en piezas.
- Script `scripts/generar_vistas_proveedores.py` las crea a partir de `dim_articulo_proveedor` (`ES_PROV_PREDET = 'S'`).

Compartir: un reporte Looker por proveedor (copia de plantilla) con acceso "solo ver" a la cuenta Google del proveedor. Evitar "cualquiera con la liga". Decisión pendiente tuya: si exponer **importe** de sell-out o solo **piezas** (con importe un proveedor puede inferir tu margen sobre su producto).

## 7. Estimación de esfuerzo

### Escenario A: 2 horas (MVP interno, corre desde tu Mac)
| # | Tarea | Min |
|---|---|---|
| 1 | Rama `feat/etl-endpoints` en la API: `routers/etl.py` con solo `ventas-articulo` (proc por semana) + registro en `main.py` | 40 |
| 2 | ETL: `fact_ventas_articulo` con esquema explícito, backfill semanal 3 años, fix `tipo_docto`, quitar `pv_tickets`, snapshot idempotente, `BACKFILL_START_DATE` | 40 |
| 3 | Levantar API de la rama en tu Mac contra Firebird (el MCP ya llega), correr backfill (~157 requests, 10–20 min en paralelo), crear `v_ventas_diarias_articulo` y `v_inventario_actual` | 30 |
| 4 | Conectar Looker Studio a las 2 vistas, 1 página | 10 |
| | **Total** | **120** |

Queda fuera: compras, proveedores, cron en servidor, PR/tests, inventario mensual, costos en vistas. Riesgo principal: el proc `MARGEN_DOCTOS_PER_XD` puede tardar más de lo esperado por semana; mitigación: bajar el chunk a 1 día (1,095 requests, ~10 min por rate limit).

### Escenario B: 1 día (8 h, todo lo de este plan menos fase 2)
| Bloque | h | Entregable |
|---|---|---|
| API | 2.5 | Router `/etl` con 6 endpoints, tests unitarios, PR de `feat/etl-endpoints` |
| ETL | 2.5 | Esquemas, backfill chunked reanudable, ventana rodante 45 d, saldos mensuales, dedup, `EMPRESA`, retención, tests |
| BigQuery | 1.0 | 2 datasets, 6 vistas internas, script de vistas por proveedor, expiración de particiones |
| Deploy | 1.0 | `docker-compose` servicio `etl` + cron en servidor API, `.env`, service account, primera corrida completa verificada contra Microsip |
| Looker | 1.0 | Reporte interno 3 páginas (ventas, inventario, compras) + plantilla proveedor con 1 proveedor de prueba |
| **Total** | **8.0** | |

El backfill (~20–40 min) corre en paralelo mientras se hacen vistas y Looker.

### Fase 2 (después, ~4 h)
Órdenes de compra pendientes, pedidos pendientes de surtir, CxC por cliente/vendedor, conversión de cotizaciones, multi-empresa (DIASA, PCC).

## 8. Datos adicionales sugeridos

### Para compras
| Dato | Fuente | Fase | Para qué |
|---|---|---|---|
| Sell-in por proveedor | `compras-partidas` | B | comparar contra sell-out, negociar |
| Inventario mensual 36 meses | `SALDOS_IN` | B | rotación, cobertura, estacionalidad |
| Días de inventario por artículo/almacén | derivado | B | qué reordenar, qué está sobrestockeado |
| Último costo, proveedor predeterminado, lead time promedio | `PRECIOS_COMPRA` | B | planear reorden |
| Artículos sin venta N meses con existencia | derivado | B | liquidar / dejar de comprar |
| Órdenes de compra en tránsito (`UNIDADES_A_REC`) | `DOCTOS_CM` 'O' | 2 | disponible proyectado |
| Pedidos pendientes de surtir (`UNIDADES_A_SURTIR`) | `DOCTOS_VE` 'P' | 2 | demanda insatisfecha / backorder |
| Devoluciones a proveedor | `DOCTOS_CM` 'D' | B | calidad por proveedor |

### Para ventas
| Dato | Fuente | Fase | Para qué |
|---|---|---|---|
| Margen por artículo/línea/cliente/vendedor | proc margen | B | qué vender, comisiones |
| Clientes activos vs inactivos, última compra, frecuencia | `ventas_documentos` | B | recuperar clientes |
| Ticket promedio y # facturas por cliente/mes | derivado | B | tendencia por cuenta |
| % devoluciones por artículo y cliente | `fact_ventas_articulo` | B | problemas de producto/cliente |
| Ventas por ruta/vendedor | existe `/ventas/por-ruta` | B | seguimiento equipo |
| Cartera vencida por cliente/vendedor | existe `/cxc/saldos` | 2 | cobranza |
| Tasa de conversión cotización → factura | `DOCTOS_VE_LIGAS` | 2 | efectividad |
| Precio promedio vs lista, descuento efectivo | `ventas-partidas` + `PRECIOS_ARTICULOS` | 2 | fuga de margen |

## 9. Riesgos y verificaciones previas
1. **Rendimiento de `MARGEN_DOCTOS_PER_XD` por semana**: medir con 1 semana de 2025 antes de fijar el chunk.
2. **Semántica de `SIGNO` y `V_TIPO_VENTA`**: validar un mes contra el reporte de margen de Microsip (importe y costo deben cuadrar).
3. **Tamaño del snapshot de existencias** con 23 almacenes: si supera ~100k filas/día, bajar retención a 200 días.
4. **Rate limit 120/min** alcanza (backfill ~250 requests, incremental ~20/noche). Si estorba, `rate_limit_per_minute` más alto solo para la key del ETL.
5. **Timeout**: endpoints bulk pueden pasar de 30 s; subir `timeout` del cliente a 120 s y revisar `fb_timeout`.
6. **Cancelaciones**: la ventana rodante de 45 días las cubre; cancelaciones de más de 45 días atrás no se reflejan (aceptable, o correr un re-backfill mensual).
7. **Precio de venta a proveedores**: decisión pendiente (piezas vs importe).

## 11. Estado al 2026-09-10 (escenario B ejecutado)

| Bloque | Estado | Detalle |
|---|---|---|
| Verificaciones | ✅ | Proc 19 s / semana; importe y devoluciones cuadran contra `DOCTOS_VE`; `UTILIDAD` también lleva `SIGNO`; llave única confirmada |
| API | ✅ código | Rama `feat/etl-endpoints`, commit `e91b4c5`, 5 endpoints, 24 tests, ruff/mypy limpios. SQL validado contra Firebird real. **Falta:** push, PR, merge y deploy en el servidor |
| ETL | ✅ código | Commits en `claude/microsip-looker-studio-api-965182`; 67 tests; imagen Docker construida. **Falta:** correr `ensure-tables`, `backfill`, `views` (bloqueado por credenciales GCP y por el deploy de la API) |
| BigQuery | ⏸ | Service account `microsip-etl@lookerstudio-microsip` responde "account not found" (eliminada) y el login de gcloud expiró. Requiere `gcloud auth login`, `gcloud auth application-default login` y nueva service account/llave |
| Deploy | ✅ archivos | `deploy/docker-compose.yml`, `deploy/run_nightly.sh`, `deploy/crontab.example`. **Falta:** instalar en el servidor tras el merge de la API |
| Looker | ✅ guía | `docs/looker_studio.md` con ligas de creación y contenido por página. **Falta:** construir los reportes una vez cargados los datos |

Hallazgo adicional: `/inventarios/existencias` devuelve filas de `SALDOS_IN`
(deltas mensuales), no existencias; la tabla `inventario_existencias` v1
estaba mal. v2 la recalcula desde `/etl/saldos-iniciales`.

## 10. Orden de ejecución sugerido
1. Verificaciones 1 y 2 (15 min, solo lectura).
2. API: rama + router + tests.
3. ETL: esquemas + backfill + fixes.
4. Backfill completo desde el Mac (o desde el servidor si ya está el Docker).
5. Vistas BQ + Looker interno.
6. Deploy cron en servidor.
7. Vistas y reporte de proveedor piloto.
