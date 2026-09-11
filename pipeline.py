"""ETL pipeline orchestrator: extract from API → load to BigQuery.

Two entry points:

- ``run_nightly``: catalogs, sales headers and fact tables for a rolling
  window (default 45 days), monthly balances for the current and previous
  month, and today's inventory snapshot. Errors per table are collected and
  raised at the end so cron sees a non-zero exit code.
- ``run_backfill``: loads history from ``backfill_start`` to today in chunks,
  resuming from the last successful chunk recorded in ``_etl_sync_state``.
  Aborts on the first error so the next run resumes cleanly.
"""

from __future__ import annotations

import calendar
import csv
import logging
import re
from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
from google.api_core.exceptions import GoogleAPIError

from api_client import MicrosipClient
from bq_loader import BigQueryLoader
from schemas import (
    CATALOGOS_AUX,
    CATALOGS,
    DIM_ARTICULO_CLAVES,
    DIM_ARTICULO_PROVEEDOR,
    DIM_FORMATO_VENTA,
    FACT_SALDOS_MENSUALES,
    FACTS,
    INVENTARIO_EXISTENCIAS,
    VENTAS_DOCUMENTOS,
    VENTAS_ENDPOINTS,
    FETCH_CHUNK,
    FETCH_KEYSET,
    TableConfig,
    coerce_row,
)
from sync_state import SyncStateManager

logger = logging.getLogger(__name__)

_RECOVERABLE = (httpx.HTTPError, GoogleAPIError, RuntimeError)

PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_FORMATOS_VENTA_CSV = "config/formatos_venta.csv"
_TRUE_VALUES = {"true", "1", "si", "sí", "s", "yes", "y", "t"}
_FALSE_VALUES = {"false", "0", "no", "n", "f", ""}


class PipelineError(Exception):
    """One or more tables failed; see logs and _etl_sync_state."""


# --- Date helpers ---


def iter_chunks(start: date, end: date, days: int) -> Iterator[tuple[date, date]]:
    """Yield inclusive (chunk_start, chunk_end) ranges covering [start, end]."""
    if days < 1:
        raise ValueError("days must be >= 1")
    cur = start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=days - 1), end)
        yield cur, chunk_end
        cur = chunk_end + timedelta(days=1)


def iter_months(start: date, end: date) -> Iterator[date]:
    """Yield the first day of every month from start's month to end's month."""
    cur = start.replace(day=1)
    last = end.replace(day=1)
    while cur <= last:
        yield cur
        cur = add_months(cur, 1)


def add_months(d: date, n: int) -> date:
    month_index = d.month - 1 + n
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    return d.replace(year=year, month=month, day=1)


def month_last_day(d: date) -> int:
    return calendar.monthrange(d.year, d.month)[1]


# --- Sales-format mapping (config/formatos_venta.csv) ---


def _parse_bool(value: str | None, *, field: str, line: int) -> bool:
    text = (value or "").strip().lower()
    if text in _TRUE_VALUES:
        return True
    if text in _FALSE_VALUES:
        return False
    raise ValueError(f"formatos_venta.csv line {line}: {field}={value!r} is not a boolean")


def resolve_project_path(path: str | Path) -> Path:
    """Resolve a config path relative to the repo root unless it is absolute."""
    p = Path(path)
    return p if p.is_absolute() else PROJECT_DIR / p


def load_formatos_venta(path: str | Path) -> list[dict]:
    """Read config/formatos_venta.csv into dim_formato_venta rows.

    Columns: ORDEN (int), PATRON (RE2 regex, validated with ``re``),
    FORMATO (str), INCLUIR (bool). Blank lines and rows without PATRON are
    skipped. Raises ValueError on malformed rows so the bad CSV never
    reaches BigQuery.
    """
    csv_path = resolve_project_path(path)
    rows: list[dict] = []
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        required = {"ORDEN", "PATRON", "FORMATO", "INCLUIR"}
        headers = {h.strip().upper() for h in (reader.fieldnames or [])}
        if not required <= headers:
            raise ValueError(
                f"{csv_path}: header must contain {sorted(required)}, got {sorted(headers)}"
            )
        for line, raw in enumerate(reader, start=2):
            row = {(k or "").strip().upper(): (v or "") for k, v in raw.items()}
            patron = row["PATRON"].strip()
            if not patron:
                continue
            try:
                orden = int(row["ORDEN"].strip())
            except ValueError as exc:
                raise ValueError(f"{csv_path} line {line}: ORDEN={row['ORDEN']!r} is not an int") from exc
            try:
                re.compile(patron, re.IGNORECASE)
            except re.error as exc:
                raise ValueError(f"{csv_path} line {line}: invalid PATRON {patron!r}: {exc}") from exc
            formato = row["FORMATO"].strip()
            if not formato:
                raise ValueError(f"{csv_path} line {line}: FORMATO is empty")
            rows.append(
                {
                    "ORDEN": orden,
                    "PATRON": patron,
                    "FORMATO": formato,
                    "INCLUIR": _parse_bool(row["INCLUIR"], field="INCLUIR", line=line),
                }
            )
    if not rows:
        raise ValueError(f"{csv_path}: no mapping rows")
    rows.sort(key=lambda r: r["ORDEN"])
    return rows


class Pipeline:
    """Orchestrates the ETL against one Microsip company."""

    def __init__(
        self,
        api: MicrosipClient,
        loader: BigQueryLoader,
        state: SyncStateManager,
        empresa: str,
        backfill_start: date,
        rolling_window_days: int = 45,
        ventas_chunk_days: int = 7,
        compras_chunk_days: int = 31,
        retention_days_facts: int | None = 1100,
        retention_days_snapshots: int | None = 400,
        today: date | None = None,
        formatos_venta_csv: str | Path = DEFAULT_FORMATOS_VENTA_CSV,
    ):
        self.api = api
        self.loader = loader
        self.state = state
        self.empresa = empresa
        self.backfill_start = backfill_start
        self.rolling_window_days = rolling_window_days
        self.today = today or date.today()
        self.formatos_venta_csv = resolve_project_path(formatos_venta_csv)
        self.failures: list[str] = []

        # Per-table overrides from settings
        self.chunk_days = {
            "fact_ventas_articulo": ventas_chunk_days,
            "fact_compras_partidas": compras_chunk_days,
            "ventas_documentos": VENTAS_DOCUMENTOS.chunk_days,
        }
        self.expiration_days = {
            "fact_ventas_articulo": retention_days_facts,
            "fact_compras_partidas": retention_days_facts,
            "ventas_documentos": retention_days_facts,
            "inventario_existencias": retention_days_snapshots,
        }

    # --- Entry points ---

    def ensure_tables(self):
        """Create/reconcile every explicit-schema table and its retention."""
        explicit = FACTS + [
            FACT_SALDOS_MENSUALES,
            INVENTARIO_EXISTENCIAS,
            DIM_ARTICULO_PROVEEDOR,
            DIM_ARTICULO_CLAVES,
            DIM_FORMATO_VENTA,
        ]
        for config in explicit:
            self.loader.ensure_table(self._with_retention(config))

    def run_nightly(self):
        """Daily run: catalogs → headers → facts (rolling) → saldos → snapshot."""
        logger.info("=== ETL nightly started (empresa=%s) ===", self.empresa)
        self.ensure_tables()
        self.sync_catalogs()
        self.sync_transactions()
        self.sync_facts()
        self.sync_saldos()
        self.sync_snapshots()
        logger.info("=== ETL nightly finished ===")
        self._raise_if_failed()

    # Backwards compatible alias
    run_all = run_nightly

    def run_backfill(
        self,
        start: date | None = None,
        end: date | None = None,
        tables: list[str] | None = None,
    ):
        """Load history in chunks, resuming per table from _etl_sync_state."""
        end = end or self.today
        logger.info("=== ETL backfill %s..%s ===", start or "(resume)", end)
        self.ensure_tables()
        wanted = set(tables) if tables else None

        def selected(name: str) -> bool:
            return wanted is None or name in wanted

        if selected("catalogs"):
            self.sync_catalogs()
        if selected(VENTAS_DOCUMENTOS.bq_table):
            self._backfill_headers(start, end)
        for config in FACTS:
            if selected(config.bq_table):
                self._backfill_fact(config, start, end)
        if selected(FACT_SALDOS_MENSUALES.bq_table):
            self._backfill_saldos(start, end)
        if selected(INVENTARIO_EXISTENCIAS.bq_table):
            self.sync_snapshots()
        logger.info("=== ETL backfill finished ===")
        self._raise_if_failed()

    # --- Steps ---

    def sync_catalogs(self):
        """Full refresh all dimension tables (API catalogs + local CSV)."""
        logger.info("--- Syncing catalogs ---")
        for config in CATALOGS + CATALOGOS_AUX + [DIM_ARTICULO_PROVEEDOR, DIM_ARTICULO_CLAVES]:
            self._guard(config.bq_table, self._sync_full_refresh, config)
        self._guard(DIM_FORMATO_VENTA.bq_table, self._sync_formatos_venta)

    def sync_transactions(self):
        """Sales document headers for the rolling window (MERGE)."""
        logger.info("--- Syncing ventas_documentos (rolling window) ---")
        start = self.today - timedelta(days=self.rolling_window_days)
        self._guard(
            VENTAS_DOCUMENTOS.bq_table, self._sync_headers_range, start, self.today
        )

    def sync_facts(self):
        """Fact tables for the rolling window (DELETE + INSERT per chunk)."""
        logger.info("--- Syncing facts (rolling window %d days) ---", self.rolling_window_days)
        start = self.today - timedelta(days=self.rolling_window_days)
        for config in FACTS:
            self._guard(config.bq_table, self._sync_fact_range, config, start, self.today)

    def sync_saldos(self):
        """Monthly balances: current and previous month."""
        logger.info("--- Syncing fact_saldos_mensuales (current + previous month) ---")
        months = [add_months(self.today, -1), self.today.replace(day=1)]
        self._guard(FACT_SALDOS_MENSUALES.bq_table, self._sync_saldos_months, months)

    def sync_snapshots(self):
        """Today's inventory snapshot (idempotent per partition)."""
        logger.info("--- Syncing inventario_existencias snapshot ---")
        self._guard(INVENTARIO_EXISTENCIAS.bq_table, self._sync_snapshot)

    # --- Backfill internals ---

    def _backfill_headers(self, start: date | None, end: date):
        config = VENTAS_DOCUMENTOS
        start = self._resume_from(config.bq_table, start)
        if start > end:
            logger.info("%s backfill already complete", config.bq_table)
            return
        for a, b in iter_chunks(start, end, self.chunk_days[config.bq_table]):
            self._sync_headers_range(a, b, state_key=f"{config.bq_table}:backfill")

    def _backfill_fact(self, config: TableConfig, start: date | None, end: date):
        start = self._resume_from(config.bq_table, start)
        if start > end:
            logger.info("%s backfill already complete", config.bq_table)
            return
        self._sync_fact_range(config, start, end, state_key=f"{config.bq_table}:backfill")

    def _backfill_saldos(self, start: date | None, end: date):
        """Opening balance the month before ``start`` + every month to ``end``."""
        config = FACT_SALDOS_MENSUALES
        key = f"{config.bq_table}:backfill"
        explicit = start is not None
        start = self._resume_from(config.bq_table, start)
        first_month = start.replace(day=1)
        if start > end:
            logger.info("%s backfill already complete", config.bq_table)
            return

        resumed = not explicit and self.state.get_last_sync_date(key) is not None
        if not resumed:
            opening = add_months(first_month, -1)
            self._sync_saldo_inicial(opening)
            self.state.record_sync(key, opening, 0, "success")

        for month in iter_months(first_month, end):
            self._sync_saldos_months([month])
            self.state.record_sync(key, add_months(month, 1) - timedelta(days=1), 0, "success")

    def _resume_from(self, table: str, start: date | None) -> date:
        if start is not None:
            return start
        last = self.state.get_last_sync_date(f"{table}:backfill")
        if last:
            resume = last + timedelta(days=1)
            logger.info("%s: resuming backfill from %s", table, resume)
            return resume
        return self.backfill_start

    # --- Sync internals ---

    def _sync_full_refresh(self, config: TableConfig):
        logger.info("Syncing %s from %s %s", config.bq_table, config.endpoint, config.api_params or "")
        if config.fetch == FETCH_KEYSET:
            rows = self.api.fetch_keyset(config.endpoint, config.api_params or None)
        elif config.fetch == FETCH_CHUNK:
            rows = self.api.fetch_chunk(config.endpoint, config.api_params)
        else:
            rows = self.api.fetch_all(config.endpoint, config.api_params or None)
        rows = self._finalize(rows, config)
        self.loader.load_full_refresh(config.bq_table, rows, config.schema)
        self.state.record_sync(config.bq_table, self.today, len(rows), "success")

    def _sync_formatos_venta(self):
        """dim_formato_venta from config/formatos_venta.csv (full refresh)."""
        config = DIM_FORMATO_VENTA
        logger.info("Syncing %s from %s", config.bq_table, self.formatos_venta_csv)
        try:
            rows = load_formatos_venta(self.formatos_venta_csv)
        except (OSError, ValueError) as exc:
            # Recoverable: the rest of the catalogs must still run.
            raise RuntimeError(f"{config.bq_table}: {exc}") from exc
        rows = self._finalize(rows, config)
        self.loader.load_full_refresh(config.bq_table, rows, config.schema)
        self.state.record_sync(config.bq_table, self.today, len(rows), "success")

    def _sync_headers_range(self, start: date, end: date, state_key: str | None = None):
        """ventas_documentos: 5 endpoints → 1 table, MERGE on DOCTO_VE_ID."""
        config = VENTAS_DOCUMENTOS
        logger.info("Syncing %s %s..%s", config.bq_table, start, end)
        params = {"fecha_inicio": start.isoformat(), "fecha_fin": end.isoformat()}
        all_rows: list[dict] = []
        for endpoint, tipo_docto in VENTAS_ENDPOINTS:
            rows = self.api.fetch_all(endpoint, params)
            for row in rows:
                row.setdefault("TIPO_DOCTO", tipo_docto)
            logger.info("  %s (%s): %d records", endpoint, tipo_docto, len(rows))
            all_rows.extend(rows)
        all_rows = self._finalize(all_rows, config)
        self.loader.load_incremental(
            config.bq_table,
            all_rows,
            config.primary_key,
            config.partition_field,
            config.clustering_fields,
        )
        self.state.record_sync(state_key or config.bq_table, end, len(all_rows), "success")

    def _sync_fact_range(
        self, config: TableConfig, start: date, end: date, state_key: str | None = None
    ):
        """Fetch [start, end] in chunks; each chunk replaces its date range."""
        days = self.chunk_days.get(config.bq_table, config.chunk_days)
        for a, b in iter_chunks(start, end, days):
            params = {"fecha_inicio": a.isoformat(), "fecha_fin": b.isoformat()}
            if config.fetch == FETCH_CHUNK:
                rows = self.api.fetch_chunk(config.endpoint, params)
            else:
                rows = self.api.fetch_keyset(config.endpoint, params)
            rows = self._finalize(rows, config)
            self.loader.load_replace_range(
                config.bq_table, rows, config.schema, config.partition_field, a, b
            )
            logger.info("  %s %s..%s: %d rows", config.bq_table, a, b, len(rows))
            self.state.record_sync(state_key or config.bq_table, b, len(rows), "success")

    def _sync_saldos_months(self, months: list[date]):
        config = FACT_SALDOS_MENSUALES
        rows: list[dict] = []
        for month in months:
            data = self.api.fetch_chunk(
                config.endpoint, {"anio": month.year, "mes": month.month}
            )
            for row in data:
                row["PERIODO"] = month.isoformat()
                row["ES_SALDO_INICIAL"] = False
            rows.extend(data)
            logger.info("  saldos %s: %d rows", month.isoformat()[:7], len(data))
        rows = self._finalize(rows, config)
        self.loader.load_replace_months(
            config.bq_table, rows, config.schema, config.partition_field, months
        )
        self.state.record_sync(config.bq_table, months[-1], len(rows), "success")

    def _sync_saldo_inicial(self, month: date):
        """Cumulative balance through ``month`` stored as one opening row."""
        config = FACT_SALDOS_MENSUALES
        hasta = month.strftime("%Y-%m")
        data = self.api.fetch_chunk("/etl/saldos-iniciales", {"hasta": hasta})
        rows = []
        for r in data:
            rows.append(
                {
                    "PERIODO": month.isoformat(),
                    "ANO": month.year,
                    "MES": month.month,
                    "ULTIMO_DIA": month_last_day(month),
                    "ARTICULO_ID": r["ARTICULO_ID"],
                    "ALMACEN_ID": r["ALMACEN_ID"],
                    "ENTRADAS_UNIDADES": r.get("EXISTENCIA"),
                    "SALIDAS_UNIDADES": 0,
                    "ENTRADAS_COSTO": r.get("COSTO"),
                    "SALIDAS_COSTO": 0,
                    "ES_SALDO_INICIAL": True,
                }
            )
        rows = self._finalize(rows, config)
        self.loader.load_replace_months(
            config.bq_table, rows, config.schema, config.partition_field, [month]
        )
        logger.info("  saldo inicial %s: %d rows", hasta, len(rows))

    def _sync_snapshot(self):
        config = INVENTARIO_EXISTENCIAS
        hasta = self.today.strftime("%Y-%m")
        data = self.api.fetch_chunk(config.endpoint, {"hasta": hasta})
        snapshot_date = self.today.isoformat()
        for row in data:
            row["_snapshot_date"] = snapshot_date
        rows = self._finalize(data, config)
        self.loader.load_partition(config.bq_table, rows, config.schema, self.today)
        self.state.record_sync(config.bq_table, self.today, len(rows), "success")

    # --- Helpers ---

    def _finalize(self, rows: list[dict], config: TableConfig) -> list[dict]:
        """Add EMPRESA/_synced_at and coerce to the explicit schema if any."""
        now = datetime.now(timezone.utc).isoformat()
        for row in rows:
            row["EMPRESA"] = self.empresa
            row["_synced_at"] = now
        if config.schema:
            return [coerce_row(r, config.schema) for r in rows]
        return rows

    def _with_retention(self, config: TableConfig) -> TableConfig:
        days = self.expiration_days.get(config.bq_table, config.expiration_days)
        if days == config.expiration_days:
            return config
        return TableConfig(**{**config.__dict__, "expiration_days": days})

    def _guard(self, table: str, fn, *args, **kwargs):
        """Run a step; recoverable errors are recorded and collected."""
        try:
            fn(*args, **kwargs)
        except _RECOVERABLE:
            logger.exception("Failed to sync %s", table)
            self.state.record_sync(table, self.today, 0, "error")
            self.failures.append(table)
        except Exception:
            logger.exception("Unexpected fatal error syncing %s", table)
            raise

    def _raise_if_failed(self):
        if self.failures:
            failed = ", ".join(self.failures)
            self.failures = []
            raise PipelineError(f"Tables failed: {failed}")
