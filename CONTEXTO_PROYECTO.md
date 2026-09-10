> **Nota (2026-09-10):** este documento describe la versión 1. La versión 2 (endpoints `/etl`, hechos con esquema explícito, ventana rodante, snapshots idempotentes, vistas para Looker y deploy Docker+cron) está documentada en [README.md](README.md) y [PLAN_BI_V2.md](PLAN_BI_V2.md).

# Microsip ETL — Contexto del Proyecto

**Ubicación:** `microsip-etl/`
**Estado:** Funcional con fixes aplicados, pendiente hardening para producción
**Fecha de creación:** Marzo 2026

---

## Qué es

Pipeline ETL (Extract, Transform, Load) que extrae datos de Microsip a través de la API REST y los carga en Google BigQuery para análisis y reporting.

## Problema que resuelve

Los datos de Microsip viven en Firebird y no son accesibles para herramientas de analytics modernas. Este ETL mueve los datos a BigQuery donde se pueden crear dashboards, reportes y análisis con herramientas como Looker Studio, Data Studio o consultas SQL directas.

## Arquitectura

```
API de Microsip (FastAPI) → api_client.py → pipeline.py → bq_loader.py → BigQuery
                                                ↕
                                          sync_state.py (tracking)
```

- **api_client.py:** Cliente HTTP con retry, backoff exponencial, paginación automática (safety valve: 10K páginas max)
- **pipeline.py:** Orquestador ETL. Soporta dos modos:
  - **Incremental:** Solo carga registros nuevos/modificados desde la última sincronización
  - **Snapshot:** Toma una foto completa de los datos con `_snapshot_date` para historial
- **bq_loader.py:** Operaciones BigQuery (crear dataset/tabla, cargar datos via NDJSON, merge/upsert)
- **sync_state.py:** Rastrea la fecha de última sincronización por tabla en una tabla BigQuery
- **schemas.py:** Define la configuración de cada tabla (endpoint, primary keys, particionamiento, clustering)

## Entry points

- **`main.py`** — CLI para ejecución local o en Docker
- **`cloud_function.py`** — Entry point para Google Cloud Functions (con logging JSON estructurado)

## Stack técnico

- **Python 3.11+**
- **httpx** — Cliente HTTP async
- **google-cloud-bigquery** — SDK de BigQuery
- **pydantic-settings** — Configuración desde `.env`
- **Docker** — Listo para Cloud Run
- **Google Cloud Functions** — Deployment serverless

## Fixes ya aplicados (ver CLAUDE_CODE_HANDOFF.md)

Se aplicaron 15 fixes en una revisión de código, incluyendo:
- Fix de `UnboundLocalError` en retry
- Fix de `WRITE_TRUNCATE` que destruía historial de snapshots
- Fix de `except Exception: pass` que ocultaba errores
- Validación de SQL injection en nombres de tablas
- Validación de configuración (API key, project ID)
- Staging tables con UUID para evitar race conditions
- 20 unit tests

## Trabajo pendiente (priorizado)

**Alta prioridad:**
- Schemas explícitos en BigQuery (actualmente usa autodetect)
- Deduplicación de snapshots (si corre 2 veces el mismo día, duplica filas)
- Tests de integración con BigQuery real o emulador

**Media prioridad:**
- Monitoring y alertas (Cloud Monitoring, Pub/Sub)
- CI/CD pipeline (GitHub Actions o Cloud Build)
- Considerar Cloud Run en lugar de Cloud Functions para ETLs largos (>9 min)

**Baja prioridad:**
- Retry con tenacity en lugar de implementación manual
- Validación de datos a nivel de fila
- Secrets Manager en lugar de env vars

## Dependencia crítica

Depende al 100% de la **API de Microsip** (`API de Microsip/`). Si la API está caída, el ETL no puede extraer datos.

## Archivos clave

| Archivo | Propósito |
|---------|-----------|
| `config.py` | Settings con validación (pydantic-settings) |
| `api_client.py` | Cliente HTTP con retry + paginación |
| `bq_loader.py` | Operaciones BigQuery |
| `sync_state.py` | Tracking de última sincronización |
| `schemas.py` | Configuración de tablas (endpoints, keys, partitioning) |
| `pipeline.py` | Orquestador ETL |
| `main.py` | CLI entry point |
| `cloud_function.py` | Cloud Function entry point |
| `CLAUDE_CODE_HANDOFF.md` | Reporte detallado de fixes y trabajo pendiente |
| `tests/` | 20 unit tests (3 archivos) |
