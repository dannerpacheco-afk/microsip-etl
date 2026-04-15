# Microsip ETL — Code Review & Fix Handoff Report

## Project Overview
ETL pipeline that extracts data from Microsip REST API and loads it into BigQuery.
Architecture: `api_client.py` → `pipeline.py` → `bq_loader.py`, with `sync_state.py` tracking last sync dates and `schemas.py` defining table configs.

Entry points: `main.py` (CLI) and `cloud_function.py` (Cloud Functions).

## What Was Done (Fixes Applied)

### Critical Bugs Fixed

1. **`api_client.py` — `UnboundLocalError` in `_request_with_retry`**
   - **Problem:** If `max_retries=0`, the loop never ran and `response` was never assigned. Also, exhausted 429 retries returned the 429 response as "successful."
   - **Fix:** Added `max_retries >= 1` validation. After exhausting retries, explicitly raises `httpx.HTTPStatusError`. Added `Retry-After` header support and capped backoff at 30s.

2. **`pipeline.py` — Snapshot `WRITE_TRUNCATE` destroyed history**
   - **Problem:** `_sync_snapshot` used `load_full_refresh` (WRITE_TRUNCATE), which deleted all previous snapshot rows each run, despite adding `_snapshot_date` for historical tracking.
   - **Fix:** Added new `load_append()` method to `BigQueryLoader` using `WRITE_APPEND`. `_sync_snapshot` now calls `load_append` instead of `load_full_refresh`.

3. **`bq_loader.py` — `except Exception: pass` in `_ensure_target_from_staging`**
   - **Problem:** Caught ALL exceptions (network, auth, timeout) and assumed "table doesn't exist."
   - **Fix:** Changed to `except NotFound` from `google.api_core.exceptions`.

4. **`sync_state.py` — `.format()` for table name (potential SQL injection pattern)**
   - **Problem:** Used Python `.format()` to inject `self.table_id` into SQL. While the value came from config, this pattern is dangerous if copied.
   - **Fix:** Added validation in `__init__` that all parts of `dataset_ref` match `^[a-zA-Z0-9_\-.]+$`. Changed `except Exception` to `except NotFound`.

### Security Fixes

5. **`config.py` — No validation of required settings**
   - **Fix:** Added `@field_validator` for `microsip_api_key` (rejects empty or placeholder values), `gcp_project_id` (rejects empty), and `microsip_api_url` (warns on non-localhost HTTP).

6. **`bq_loader.py` — Added `_validate_identifier()` function**
   - All table names, column names, primary keys, partition fields, and clustering fields are validated against `^[a-zA-Z0-9_]+$` before being used in SQL strings. This prevents SQL injection via any path.

### Robustness Fixes

7. **`bq_loader.py` — Temp file cleanup with `try/finally`**
   - Both `load_full_refresh`, `load_append`, and `load_incremental` now clean up NDJSON temp files in `finally` blocks.

8. **`bq_loader.py` — Race condition in staging table**
   - **Problem:** Concurrent runs would overwrite each other's `_staging_{table}`.
   - **Fix:** Staging table name now includes a UUID suffix: `_staging_{table}_{uuid8}`.

9. **`api_client.py` — Infinite pagination safety valve**
   - Added `max_pages` parameter (default 10,000). Pagination stops with an error log if this limit is hit.

10. **`pipeline.py` — Granular exception handling**
    - Changed from `except Exception` (catches everything silently) to `except (httpx.HTTPStatusError, GoogleAPIError)` for recoverable errors. Unexpected errors now re-raise to halt the pipeline.

11. **`bq_loader.py` — Context manager for BigQuery client**
    - Added `__enter__`/`__exit__`/`close()` methods. Updated `main.py` and `cloud_function.py` to use `with BigQueryLoader(...) as loader:`.

### Infrastructure

12. **`cloud_function.py` — Structured JSON logging**
    - Added `_CloudLoggingFormatter` that outputs JSON compatible with Google Cloud Logging structured logs.

13. **`Dockerfile` added**
    - Multi-stage, non-root user, pip cache disabled, ready for `docker build -t microsip-etl .`.

14. **`config.py` — New `max_pages` setting** (default 10,000, configurable via `MAX_PAGES` env var).

### Tests Added

15. **20 unit tests** across 3 test files:
    - `tests/test_api_client.py` (10 tests): retry logic, pagination, safety valve, context manager
    - `tests/test_config.py` (7 tests): validation of API key, project ID, HTTP warnings, defaults
    - `tests/test_bq_loader.py` (3 tests): identifier validation, NDJSON file writing

    Run with: `pytest tests/ -v -p no:cacheprovider`

## Remaining Work (Not Done Yet)

These items were identified but NOT implemented. They represent next steps:

### High Priority

- [ ] **Explicit BigQuery schemas in `schemas.py`**: Currently relies on `autodetect=True`. Should define explicit `SchemaField` lists per table (at minimum for `ventas_documentos` and `pv_tickets`) to prevent schema drift. The `load_full_refresh` and `load_append` methods already accept an optional `schema` parameter.

- [ ] **Snapshot deduplication**: With the fix to use `WRITE_APPEND`, if the ETL runs twice in one day, you'll get duplicate rows for the same `_snapshot_date`. Options:
  - Add a `DELETE WHERE _snapshot_date = @today` before the append
  - Use a MERGE pattern similar to incremental loads
  - Partition by `_snapshot_date` and use `WRITE_TRUNCATE` on just that partition

- [ ] **Integration tests**: Current tests only cover unit-level logic with mocks. Need integration tests that hit a real (or emulated) BigQuery instance. Consider using `bigquery-emulator` Docker image.

### Medium Priority

- [ ] **Monitoring & alerting**: No health checks, metrics, or failure notifications. Suggestions:
  - Cloud Monitoring alerting policy on Cloud Function errors
  - Pub/Sub notification on pipeline failure
  - Dashboard showing sync state table (last_run_at, status per table)

- [ ] **CI/CD pipeline**: Add `cloudbuild.yaml` or GitHub Actions for:
  - Running tests on PR
  - Building and deploying the Cloud Function
  - Docker image build/push to Artifact Registry

- [ ] **Cloud Run alternative**: For longer-running ETLs, Cloud Run (with 60min timeout) may be better than Cloud Functions (9min max). The `Dockerfile` is already compatible.

### Low Priority

- [ ] **Retry decorator**: Replace manual `_request_with_retry` with `tenacity` library for more robust retry patterns (jitter, configurable strategies).

- [ ] **Data validation**: Add row-level validation after API fetch (e.g., ensure primary keys are non-null, dates are valid) before loading to BQ. Consider using `pydantic` models for each table.

- [ ] **Secrets management**: Move API key from `.env` / env vars to Google Secret Manager for production deployments.

## File Map (Quick Reference)

```
microsip-etl/
├── config.py              # Settings with validation (pydantic-settings)
├── api_client.py          # HTTP client with retry + pagination
├── bq_loader.py           # BigQuery dataset/table/load operations
├── sync_state.py          # Tracks last sync date per table
├── schemas.py             # Table configs (endpoints, keys, partitioning)
├── pipeline.py            # ETL orchestrator
├── main.py                # CLI entry point
├── cloud_function.py      # Cloud Function entry point (JSON logging)
├── Dockerfile             # Docker build for Cloud Run / local
├── requirements.txt       # Python dependencies
├── .env.example           # Template for environment variables
├── .gitignore
└── tests/
    ├── conftest.py        # Shared test setup (env vars)
    ├── test_api_client.py # 10 tests
    ├── test_config.py     # 7 tests
    └── test_bq_loader.py  # 3 tests
```
