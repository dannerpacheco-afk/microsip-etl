# Microsip ETL → BigQuery → Looker Studio

Extrae ventas, compras e inventario de Microsip **a través de la API REST**
(nunca contra Firebird directo) y los carga en BigQuery con un historial
rodante de 3 años. Looker Studio se conecta a vistas, no a tablas.

Contexto y decisiones: [PLAN_BI_V2.md](PLAN_BI_V2.md).

```
Firebird ──(usuario solo lectura)──> API de Microsip /api/v1/etl/* ──> microsip-etl ──> BigQuery ──> Looker Studio
                                                                        (Docker + cron)     microsip / microsip_proveedores
```

## Requisitos

- API de Microsip con el router `/etl` (rama `feat/etl-endpoints`, ver `docs/etl_endpoints.md` en ese repo).
- API key con scope `read` y acceso a la empresa configurada.
- Proyecto GCP con BigQuery. Credencial: service account con
  `roles/bigquery.dataEditor` + `roles/bigquery.jobUser` (`credentials.json`) o
  Application Default Credentials.
- Python 3.12+ (`uv venv && uv pip install -r requirements.txt`) o Docker.

## Configuración

Copia `.env.example` a `.env`. Variables clave:

| Variable | Default | Uso |
|---|---|---|
| `MICROSIP_API_URL` | `http://localhost:8000/api/v1` | URL de la API |
| `MICROSIP_EMPRESA` | `ALMACENES PACHECO` | header `X-Empresa` y columna `EMPRESA` |
| `GCP_PROJECT_ID`, `BQ_DATASET` | `lookerstudio-microsip`, `microsip` | destino |
| `BQ_DATASET_PROVEEDORES` | `microsip_proveedores` | vistas para proveedores |
| `BACKFILL_START_DATE` / `BACKFILL_YEARS` | vacío / `3` | inicio del histórico |
| `ROLLING_WINDOW_DAYS` | `45` | días que se recalculan cada noche |
| `VENTAS_CHUNK_DAYS`, `COMPRAS_CHUNK_DAYS` | `7`, `31` | tamaño de chunk del backfill |
| `RETENTION_DAYS_FACTS`, `RETENTION_DAYS_SNAPSHOTS` | `1100`, `400` | expiración de particiones |
| `PROVEEDORES_BI` | vacío | `PROVEEDOR_ID`s con vistas propias |

## Comandos

```bash
python main.py ensure-tables          # crea/reconcilia tablas y retención
python main.py backfill               # histórico 3 años, reanudable
python main.py backfill --from 2025-01-01 --to 2025-03-31 --tables fact_ventas_articulo
python main.py nightly                # corrida diaria (alias: all)
python main.py catalogs | transactions | facts | saldos | snapshots
python main.py views                  # vistas internas (sql/views)
python main.py proveedores-views      # vistas por proveedor (sql/proveedores)
```

`nightly` corre: catálogos → encabezados de ventas (45 d, MERGE) → hechos (45 d,
DELETE+INSERT por chunk) → saldos mensuales (mes actual y anterior) → snapshot
de existencias de hoy. Cada tabla falla de forma independiente; al final el
proceso sale con código 1 si alguna falló (cron avisa por `MAILTO`).

`backfill` guarda el avance por tabla en `_etl_sync_state`
(`<tabla>:backfill`) y reanuda desde el último chunk exitoso.

## Tablas en BigQuery (dataset `microsip`)

| Tabla | Fuente | Carga | Partición / retención |
|---|---|---|---|
| `fact_ventas_articulo` | `/etl/ventas-articulo` (proc `MARGEN_DOCTOS_PER_XD`, F − D, con costo) | reemplazo por rango de fechas | `FECHA` día, 1100 d |
| `fact_compras_partidas` | `/etl/compras-partidas` (C y D) | reemplazo por rango | `FECHA` día, 1100 d |
| `fact_saldos_mensuales` | `/etl/saldos-mensuales` + saldo inicial | reemplazo por mes | `PERIODO` mes, sin expiración |
| `inventario_existencias` | `/etl/saldos-iniciales?hasta=mes actual` | partición del día (idempotente) | `_snapshot_date`, 400 d |
| `ventas_documentos` | `/ventas/{facturas,remisiones,pedidos,cotizaciones,devoluciones}` | MERGE por `DOCTO_VE_ID` | `FECHA` día |
| `dim_articulo_proveedor` | `/etl/articulos-proveedores` | full refresh | — |
| `dim_articulos`, `dim_clientes`, `dim_proveedores`, `dim_almacenes`, `dim_vendedores`, `dim_lineas` | catálogos | full refresh | — |
| `_etl_sync_state` | ETL | append | — |

Todas las filas llevan `EMPRESA` y `_synced_at`. La tabla `pv_tickets` de la
versión anterior ya no se carga (`DOCTOS_PV` está vacío); se puede borrar.

### Vistas internas (`sql/views`)

`v_etl_estado`, `v_ventas_diarias_articulo`, `v_ventas_cliente_mes`,
`v_clientes_resumen`, `v_margen_linea_mes`, `v_inventario_actual`,
`v_inventario_mensual`, `v_compras_proveedor_mes`, `v_ultimo_costo_articulo`,
`v_articulos_sin_movimiento`. Looker Studio se conecta a estas vistas.

### Vistas para proveedores (`sql/proveedores`)

Por cada id en `PROVEEDORES_BI` se crean en `microsip_proveedores`:
`sellout_<id>`, `sellin_<id>`, `inventario_actual_<id>`,
`inventario_mensual_<id>`. Solo artículos con ese proveedor predeterminado
(`PRECIOS_COMPRA.ES_PROV_PREDET`). Sin costo, sin cliente, sin precio
unitario. Las vistas quedan registradas como *authorized views* sobre
`microsip`, así que al proveedor se le comparte únicamente su reporte de
Looker (o acceso de lectura a sus vistas), nunca el dataset interno.

## Despliegue (Docker + cron en el servidor de la API)

```bash
git clone <repo> /opt/microsip-etl && cd /opt/microsip-etl
cp .env.example .env            # editar; MICROSIP_API_URL=http://localhost:8000/api/v1
cp /ruta/credentials.json .     # service account de BigQuery
docker compose -f deploy/docker-compose.yml build
docker compose -f deploy/docker-compose.yml run --rm etl ensure-tables
docker compose -f deploy/docker-compose.yml run --rm etl backfill      # ~1 h la primera vez
docker compose -f deploy/docker-compose.yml run --rm etl views
crontab -e                      # pegar deploy/crontab.example
```

`deploy/run_nightly.sh` usa `flock` (una sola instancia), escribe
`logs/etl-YYYYMMDD.log` y conserva 30 días.

## Monitoreo

- `v_etl_estado`: última corrida por tabla y horas transcurridas. Agregar
  como página en el reporte interno de Looker.
- `_etl_sync_state.status = 'error'` marca la tabla que falló; los logs del
  contenedor tienen el traceback.

## Pruebas

```bash
.venv/bin/python -m pytest tests -q
```

## Notas de diseño

- **Venta** = facturas − devoluciones a nivel documento × artículo × almacén,
  tal como lo calcula el reporte de margen de Microsip. Remisiones cuentan
  cuando se facturan. Documentos cancelados no aparecen.
- **Cancelaciones tardías**: la ventana rodante de 45 días las recoge; una
  cancelación más antigua requiere `backfill --from` de ese rango.
- **Existencias** = suma acumulada de `SALDOS_IN` (entradas − salidas). El
  endpoint `/inventarios/existencias` devuelve deltas mensuales, no
  existencia, por eso no se usa.
- **Inventario mensual** = saldo inicial (mes previo al backfill) + deltas
  mensuales; `v_inventario_mensual` rellena meses sin movimiento.
