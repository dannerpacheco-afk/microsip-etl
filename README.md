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
| `GCP_PROJECT_ID`, `BQ_DATASET` | `lookerstudio-microsip-508301`, `microsip` | destino |
| `BQ_DATASET_PROVEEDORES` | `microsip_proveedores` | vistas para proveedores |
| `BACKFILL_START_DATE` / `BACKFILL_YEARS` | vacío / `3` | inicio del histórico |
| `ROLLING_WINDOW_DAYS` | `45` | días que se recalculan cada noche |
| `VENTAS_CHUNK_DAYS`, `COMPRAS_CHUNK_DAYS` | `7`, `31` | tamaño de chunk del backfill |
| `RETENTION_DAYS_FACTS`, `RETENTION_DAYS_SNAPSHOTS` | `1100`, `400` | expiración de particiones |
| `PROVEEDORES_BI` | vacío | `PROVEEDOR_ID`s con vistas propias |
| `FORMATOS_VENTA_CSV` | `config/formatos_venta.csv` | mapa tipo de cliente → formato de venta |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` | vacío / `587` | correo del reporte ISCAM (opcional, STARTTLS) |
| `REPORTE_ISCAM_TO` | vacío | destinatarios del reporte, separados por coma |

## Comandos

```bash
python main.py ensure-tables          # crea/reconcilia tablas y retención
python main.py backfill               # histórico 3 años, reanudable
python main.py backfill --from 2025-01-01 --to 2025-03-31 --tables fact_ventas_articulo
python main.py nightly                # corrida diaria (alias: all)
python main.py catalogs | transactions | facts | saldos | snapshots
python main.py views                  # vistas internas (sql/views)
python main.py proveedores-views      # vistas por proveedor (sql/proveedores)
python main.py reporte-mensual        # ISCAM del mes anterior → reports/ISCAM_<EMPRESA>_<YYYY-MM>.xlsx
python main.py reporte-mensual --mes 2026-08 --corte zona --salida /tmp --sin-correo
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
| `fact_ventas_articulo` | `/etl/ventas-articulo` (proc `MARGEN_DOCTOS_PER_XD`, F − D, con costo, `IMPUESTOS` e `IMPORTE_TOTAL`) | reemplazo por rango de fechas | `FECHA` día, 1100 d |
| `fact_compras_partidas` | `/etl/compras-partidas` (C y D) | reemplazo por rango | `FECHA` día, 1100 d |
| `fact_saldos_mensuales` | `/etl/saldos-mensuales` + saldo inicial | reemplazo por mes | `PERIODO` mes, sin expiración |
| `inventario_existencias` | `/etl/saldos-iniciales?hasta=mes actual` | partición del día (idempotente) | `_snapshot_date`, 400 d |
| `ventas_documentos` | `/ventas/{facturas,remisiones,pedidos,cotizaciones,devoluciones}` | MERGE por `DOCTO_VE_ID` | `FECHA` día |
| `dim_articulo_proveedor` | `/etl/articulos-proveedores` | full refresh | — |
| `dim_articulo_claves` | `/etl/claves-articulos` (clave principal, alternas = código de barras, SKU) | full refresh | cluster `ARTICULO_ID` |
| `dim_tipos_clientes`, `dim_zonas_clientes`, `dim_sucursales`, `dim_precios_empresa` | `/etl/catalogos-aux?tabla=…` | full refresh | — |
| `dim_formato_venta` | `config/formatos_venta.csv` (local) | full refresh | — |
| `dim_articulos`, `dim_clientes`, `dim_proveedores`, `dim_almacenes`, `dim_vendedores`, `dim_lineas` | catálogos | full refresh | — |
| `_etl_sync_state` | ETL | append | — |

Todas las filas llevan `EMPRESA` y `_synced_at`. La tabla `pv_tickets` de la
versión anterior ya no se carga (`DOCTOS_PV` está vacío); se puede borrar.

`ensure-tables` (y cada `nightly`) agrega como NULLABLE las columnas que
existan en `schemas.py` y falten en la tabla viva (p. ej. `IMPUESTOS`,
`IMPORTE_TOTAL`). Las filas cargadas antes quedan en NULL en esas columnas
hasta re-correr `backfill --tables fact_ventas_articulo` del rango deseado.

### Vistas internas (`sql/views`)

`v_etl_estado`, `v_ventas_diarias_articulo`, `v_ventas_cliente_mes` (ahora
con `ZONA` y `FORMATO`), `v_clientes_resumen`, `v_margen_linea_mes`,
`v_inventario_actual`, `v_inventario_mensual`, `v_compras_proveedor_mes`,
`v_ultimo_costo_articulo`, `v_articulos_sin_movimiento`,
`v_articulo_claves` (clave principal + código de barras por artículo),
`v_tipos_clientes_formato` (tipo de cliente → zona / formato / incluir),
`v_iscam_ventas_mensual` y `v_iscam_inventario_mensual` (base del reporte
ISCAM). Looker Studio se conecta a estas vistas.

### Vistas para proveedores (`sql/proveedores`)

Por cada id en `PROVEEDORES_BI` se crean en `microsip_proveedores`:
`sellout_<id>`, `sellin_<id>`, `inventario_actual_<id>`,
`inventario_mensual_<id>`. Solo artículos con ese proveedor predeterminado
(`PRECIOS_COMPRA.ES_PROV_PREDET`). Sin costo, sin cliente, sin precio
unitario. Las vistas quedan registradas como *authorized views* sobre
`microsip`, así que al proveedor se le comparte únicamente su reporte de
Looker (o acceso de lectura a sus vistas), nunca el dataset interno.

## Reporte mensual ISCAM

`python main.py reporte-mensual` genera el Excel que se entrega a ISCAM los
primeros días de cada mes con las ventas del mes anterior:

- **Ventas**: mes, corte (`--corte formato` por default; `zona` o `almacen`),
  clave principal, código de barras, descripción, unidad, proveedor
  predeterminado, piezas, importe sin impuestos, impuestos e importe con
  impuestos. Ventas netas (facturas − devoluciones), solo tipos de cliente con
  `INCLUIR = true`.
- **Inventario**: existencia y valor a costo al cierre del mes por almacén y
  artículo (`v_inventario_mensual`); se omiten renglones en cero.
- **Resumen**: totales por corte, total general, totales de inventario,
  fecha de generación y la nota "Ventas netas de devoluciones; importes en MXN".

Archivo: `reports/ISCAM_<EMPRESA sin espacios>_<YYYY-MM>.xlsx` (`--salida`
cambia el directorio; `reports/` está en `.gitignore`). `--mes YYYY-MM`
elige otro mes. Si `SMTP_HOST`, `SMTP_FROM` y `REPORTE_ISCAM_TO` están
configurados el archivo se envía por correo (STARTTLS); `--sin-correo` lo
evita. Cron: `deploy/crontab.example` lo programa el día 2 a las 03:30
(`ETL_TARGET=reporte-mensual deploy/run_nightly.sh`); en Docker el `.xlsx`
queda en `reports/` del host (volumen en `docker-compose.yml`).

### Formato de venta (`config/formatos_venta.csv`)

En esta empresa `TIPOS_CLIENTES` funciona como catálogo de rutas / zonas
("Z-1  MOSTRADOR SUSANA", "Z-7 JIMENEZ", "AUTOMAYOREO", "COBRANZA"…). El CSV
mapea cada nombre a un formato con expresiones regulares (RE2, sin
distinguir mayúsculas), evaluadas por `ORDEN`; gana la primera coincidencia y
lo que no coincide queda como `Otros` con `INCLUIR = true`:

```
ORDEN,PATRON,FORMATO,INCLUIR
10,^Z-1\s*MOSTRADOR,Tienda / Cash&Carry,true
20,^AUTOMAYOREO,Cash&Carry,true
30,COBRANZA|FLETES|DEUDORES|INCOBRABLES|CHEQUES|CHOFER|CONSUM INTERNO|BODEGA ATRASADOS,Excluir,false
40,^Z-\d+,Mayoreo tradicional,true
```

Para cambiar el mapeo se edita el CSV (sin tocar código); `catalogs` /
`nightly` lo recargan en `dim_formato_venta` y las vistas lo aplican al vuelo.
`INCLUIR = false` saca ese tipo de cliente del reporte ISCAM (no de las vistas
internas). `FORMATOS_VENTA_CSV` apunta a otra ruta si se necesita.

## Despliegue (Docker + cron en el servidor de la API)

```bash
git clone <repo> /opt/microsip-etl && cd /opt/microsip-etl
cp .env.example .env            # editar; MICROSIP_API_URL=http://localhost:8000/api/v1
cp /ruta/credentials.json .     # service account de BigQuery
docker compose -f deploy/docker-compose.yml build
docker compose -f deploy/docker-compose.yml run --rm etl ensure-tables
docker compose -f deploy/docker-compose.yml run --rm etl backfill      # ~1 h la primera vez
docker compose -f deploy/docker-compose.yml run --rm etl views
mkdir -p reports && chown 1000 reports   # salida del reporte ISCAM (uid del contenedor)
crontab -e                      # pegar deploy/crontab.example (nightly + reporte-mensual)
```

`deploy/run_nightly.sh` usa `flock` (una sola instancia), escribe
`logs/etl-YYYYMMDD.log` y conserva 30 días.

## Despliegue en macOS (Mac mini, launchd, sin Docker)

```bash
git clone git@github.com:dannerpacheco-afk/microsip-etl.git ~/microsip-etl && cd ~/microsip-etl
uv venv --python 3.12 .venv && uv pip install -p .venv/bin/python -r requirements.txt
cp .env.example .env            # editar; copiar credentials.json de la service account
.venv/bin/python main.py ensure-tables
for j in nightly reporte-mensual; do
  sed "s#__ETL_DIR__#$PWD#g" deploy/launchd/com.grupopacheco.microsip-etl.$j.plist \
    > ~/Library/LaunchAgents/com.grupopacheco.microsip-etl.$j.plist
  launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.grupopacheco.microsip-etl.$j.plist
done
```

`deploy/run_nightly_mac.sh` es el equivalente de `run_nightly.sh` con el venv
local (lock por `mkdir`, logs en `logs/`). Guía paso a paso para una sesión de
Claude en la Mac mini: [docs/setup_mac_mini.md](docs/setup_mac_mini.md).

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
- **Impuestos**: `IMPUESTOS` = IVA + IEPS por documento × artículo (con
  `SIGNO`) e `IMPORTE_TOTAL` = `IMPORTE_NETO + IMPUESTOS`, calculados en la
  API. El reporte ISCAM usa el importe con impuestos; el resto de las vistas
  sigue en importe neto.
- **Cajas**: Microsip no maneja cajas como unidad de venta en esta empresa;
  el reporte ISCAM entrega piezas (`UNIDADES`) y la unidad de venta del
  artículo.
