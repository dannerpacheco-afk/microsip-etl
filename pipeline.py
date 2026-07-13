"""ETL pipeline orchestrator: extract from API → load to BigQuery."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

import httpx
from google.api_core.exceptions import GoogleAPIError

from api_client import MicrosipClient
from bq_loader import BigQueryLoader
from schemas import (
    CATALOGS,
    EXISTENCIAS,
    SNAPSHOTS,
    VENTAS_DOCUMENTOS,
    VENTAS_ENDPOINTS,
    TableConfig,
)
from sync_state import SyncStateManager

logger = logging.getLogger(__name__)


class PipelineError(Exception):
    """Non-recoverable pipeline error that should halt execution."""


class Pipeline:
    """Orchestrates the full ETL: catalogs → transactions → snapshots."""

    def __init__(
        self,
        api: MicrosipClient,
        loader: BigQueryLoader,
        state: SyncStateManager,
        initial_lookback_days: int = 30,
    ):
        self.api = api
        self.loader = loader
        self.state = state
        self.initial_lookback_days = initial_lookback_days

    def run_all(self):
        """Run complete ETL pipeline."""
        logger.info("=== ETL pipeline started ===")
        self.sync_catalogs()
        self.sync_transactions()
        self.sync_snapshots()
        logger.info("=== ETL pipeline finished ===")

    def sync_catalogs(self):
        """Full refresh all dimension tables."""
        logger.info("--- Syncing catalogs ---")
        for config in CATALOGS:
            self._sync_full_refresh(config)

    def sync_transactions(self):
        """Incremental load for transaction tables."""
        logger.info("--- Syncing transactions ---")
        self._sync_ventas_documentos()

    def sync_snapshots(self):
        """Append snapshot tables (preserving historical data)."""
        logger.info("--- Syncing snapshots ---")
        for config in SNAPSHOTS:
            self._sync_snapshot(config)

    # --- Internal methods ---

    def _sync_full_refresh(self, config: TableConfig):
        """Extract all data from endpoint and full-refresh the BQ table."""
        try:
            logger.info("Syncing %s from %s", config.bq_table, config.endpoint)
            rows = self.api.fetch_all(
                config.endpoint,
                config.api_params or None,
                page_size=config.page_size,
                skip_failed_pages=config.skip_failed_pages,
            )
            rows = self._add_synced_at(rows)

            self.loader.load_full_refresh(config.bq_table, rows)
            self.state.record_sync(
                config.bq_table, date.today(), len(rows), "success"
            )
        except (httpx.HTTPStatusError, GoogleAPIError) as exc:
            logger.exception("Failed to sync %s", config.bq_table)
            self.state.record_sync(config.bq_table, date.today(), 0, "error")
        except Exception:
            # Unexpected errors (OOM, auth failures, etc.) — re-raise
            logger.exception(
                "Unexpected fatal error syncing %s", config.bq_table
            )
            raise

    def _sync_ventas_documentos(self):
        """Incremental sync for ventas_documentos (5 sub-endpoints → 1 table)."""
        config = VENTAS_DOCUMENTOS
        try:
            fecha_inicio, fecha_fin = self._get_date_range(config.bq_table)
            logger.info(
                "Syncing %s from %s to %s",
                config.bq_table,
                fecha_inicio,
                fecha_fin,
            )

            all_rows: list[dict] = []
            for endpoint, tipo_docto in VENTAS_ENDPOINTS:
                params = {
                    "fecha_inicio": fecha_inicio.isoformat(),
                    "fecha_fin": fecha_fin.isoformat(),
                }
                rows = self.api.fetch_all(endpoint, params)
                logger.info(
                    "  %s (%s): %d records", endpoint, tipo_docto, len(rows)
                )
                all_rows.extend(rows)

            all_rows = self._add_synced_at(all_rows)

            self.loader.load_incremental(
                config.bq_table,
                all_rows,
                config.primary_key,
                config.partition_field,
                config.clustering_fields,
            )
            self.state.record_sync(
                config.bq_table, fecha_fin, len(all_rows), "success"
            )
        except (httpx.HTTPStatusError, GoogleAPIError) as exc:
            logger.exception("Failed to sync %s", config.bq_table)
            self.state.record_sync(config.bq_table, date.today(), 0, "error")
        except Exception:
            logger.exception(
                "Unexpected fatal error syncing %s", config.bq_table
            )
            raise

    def _sync_incremental(self, config: TableConfig):
        """Incremental sync for a single-endpoint transaction table."""
        try:
            fecha_inicio, fecha_fin = self._get_date_range(config.bq_table)
            logger.info(
                "Syncing %s from %s to %s",
                config.bq_table,
                fecha_inicio,
                fecha_fin,
            )

            params = {
                "fecha_inicio": fecha_inicio.isoformat(),
                "fecha_fin": fecha_fin.isoformat(),
            }
            params.update(config.api_params)
            rows = self.api.fetch_all(
                config.endpoint,
                params,
                page_size=config.page_size,
                skip_failed_pages=config.skip_failed_pages,
            )
            rows = self._add_synced_at(rows)

            self.loader.load_incremental(
                config.bq_table,
                rows,
                config.primary_key,
                config.partition_field,
                config.clustering_fields,
            )
            self.state.record_sync(
                config.bq_table, fecha_fin, len(rows), "success"
            )
        except (httpx.HTTPStatusError, GoogleAPIError) as exc:
            logger.exception("Failed to sync %s", config.bq_table)
            self.state.record_sync(config.bq_table, date.today(), 0, "error")
        except Exception:
            logger.exception(
                "Unexpected fatal error syncing %s", config.bq_table
            )
            raise

    def _sync_snapshot(self, config: TableConfig):
        """Append snapshot with _snapshot_date for historical tracking.

        Uses WRITE_APPEND instead of WRITE_TRUNCATE to preserve previous
        snapshots. Each run adds new rows tagged with today's date.
        """
        try:
            logger.info("Syncing %s from %s", config.bq_table, config.endpoint)
            rows = self.api.fetch_all(
                config.endpoint,
                config.api_params or None,
                page_size=config.page_size,
                skip_failed_pages=config.skip_failed_pages,
            )
            today = date.today().isoformat()
            for row in rows:
                row["_snapshot_date"] = today
            rows = self._add_synced_at(rows)

            # Use load_append to preserve historical snapshot data
            self.loader.load_append(config.bq_table, rows)
            self.state.record_sync(
                config.bq_table, date.today(), len(rows), "success"
            )
        except (httpx.HTTPStatusError, GoogleAPIError) as exc:
            logger.exception("Failed to sync %s", config.bq_table)
            self.state.record_sync(config.bq_table, date.today(), 0, "error")
        except Exception:
            logger.exception(
                "Unexpected fatal error syncing %s", config.bq_table
            )
            raise

    def _get_date_range(self, table_name: str) -> tuple[date, date]:
        """Get (fecha_inicio, fecha_fin) for incremental sync."""
        last_sync = self.state.get_last_sync_date(table_name)
        fecha_fin = date.today()

        if last_sync:
            # Overlap by 1 day to catch status changes
            fecha_inicio = last_sync - timedelta(days=1)
        else:
            fecha_inicio = fecha_fin - timedelta(days=self.initial_lookback_days)

        return fecha_inicio, fecha_fin

    def _add_synced_at(self, rows: list[dict]) -> list[dict]:
        """Add _synced_at timestamp to all rows."""
        now = datetime.now(timezone.utc).isoformat()
        for row in rows:
            row["_synced_at"] = now
        return rows
