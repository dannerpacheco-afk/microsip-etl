"""Cloud Function entry point (legacy, kept for compatibility).

The supported deployment is Docker + cron on the API server (see deploy/).
A Cloud Function is limited to 9 minutes, enough for the nightly run but
not for a backfill.

Deploy with:
    gcloud functions deploy microsip-etl --gen2 --runtime=python312 \
        --trigger-http --entry-point=etl_handler --source=. \
        --memory=512MB --timeout=540s --no-allow-unauthenticated \
        --set-env-vars="MICROSIP_API_URL=...,MICROSIP_API_KEY=...,GCP_PROJECT_ID=..."
"""

from __future__ import annotations

import json
import logging
import sys

import functions_framework

from api_client import MicrosipClient
from bq_loader import BigQueryLoader
from config import settings
from pipeline import Pipeline
from sync_state import SyncStateManager


class _CloudLoggingFormatter(logging.Formatter):
    """JSON formatter compatible with Google Cloud Logging structured logs."""

    SEVERITY_MAP = {
        logging.DEBUG: "DEBUG",
        logging.INFO: "INFO",
        logging.WARNING: "WARNING",
        logging.ERROR: "ERROR",
        logging.CRITICAL: "CRITICAL",
    }

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "severity": self.SEVERITY_MAP.get(record.levelno, "DEFAULT"),
            "message": record.getMessage(),
            "logger": record.name,
        }
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry)


# Configure structured logging for Cloud Functions
handler = logging.StreamHandler(sys.stderr)
handler.setFormatter(_CloudLoggingFormatter())
logging.root.handlers = [handler]
logging.root.setLevel(
    getattr(logging, settings.log_level.upper(), logging.INFO)
)

logger = logging.getLogger(__name__)


@functions_framework.http
def etl_handler(request):
    """HTTP Cloud Function triggered by Cloud Scheduler."""
    with MicrosipClient(
        settings.microsip_api_url,
        settings.microsip_api_key,
        settings.page_size,
        settings.max_pages,
        empresa=settings.microsip_empresa,
        timeout=settings.request_timeout_seconds,
        bulk_page_size=settings.bulk_page_size,
    ) as api, BigQueryLoader(
        settings.gcp_project_id, settings.bq_dataset, settings.bq_location
    ) as loader:
        loader.ensure_dataset()
        loader.ensure_sync_state_table()

        state = SyncStateManager(loader.client, loader.dataset_ref)
        pipeline = Pipeline(
            api,
            loader,
            state,
            empresa=settings.microsip_empresa,
            backfill_start=settings.backfill_start,
            rolling_window_days=settings.rolling_window_days,
            ventas_chunk_days=settings.ventas_chunk_days,
            compras_chunk_days=settings.compras_chunk_days,
            retention_days_facts=settings.retention_days_facts,
            retention_days_snapshots=settings.retention_days_snapshots,
        )

        try:
            pipeline.run_nightly()
            return {"status": "success"}, 200
        except Exception as e:
            logger.exception("ETL pipeline failed")
            return {"status": "error", "message": str(e)}, 500
