"""Tracks last sync date per table in BigQuery."""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone

from google.api_core.exceptions import NotFound
from google.cloud import bigquery

logger = logging.getLogger(__name__)

# Only allow safe identifiers in table references
_VALID_TABLE_REF = re.compile(r"^[a-zA-Z0-9_\-.]+$")


class SyncStateManager:
    """Read/write sync state from the _etl_sync_state BigQuery table."""

    def __init__(self, client: bigquery.Client, dataset_ref: str):
        self.client = client
        # Validate the dataset_ref to prevent any injection via table_id
        parts = dataset_ref.split(".")
        for part in parts:
            if not _VALID_TABLE_REF.match(part):
                raise ValueError(f"Invalid dataset reference part: {part!r}")
        self.table_id = f"{dataset_ref}._etl_sync_state"

    def get_last_sync_date(self, table_name: str) -> date | None:
        """Return the last successful sync date for a table, or None.

        Uses parameterized queries for all user-supplied values.
        The table reference (self.table_id) is validated at __init__ time.
        """
        # table_id is safe (validated in __init__), and table_name uses @param
        query = f"""
            SELECT last_sync_date
            FROM `{self.table_id}`
            WHERE table_name = @table_name AND status = 'success'
            ORDER BY last_run_at DESC
            LIMIT 1
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "table_name", "STRING", table_name
                ),
            ]
        )

        try:
            result = self.client.query(query, job_config=job_config).result()
            for row in result:
                return row.last_sync_date
        except NotFound:
            logger.debug(
                "Sync state table not found for %s (first run?)", table_name
            )

        return None

    def record_sync(
        self, table_name: str, sync_date: date, records: int, status: str
    ):
        """Insert a sync state record (append-only for audit trail)."""
        rows = [
            {
                "table_name": table_name,
                "last_sync_date": sync_date.isoformat(),
                "last_run_at": datetime.now(timezone.utc).isoformat(),
                "records_synced": records,
                "status": status,
            }
        ]
        errors = self.client.insert_rows_json(self.table_id, rows)
        if errors:
            logger.error("Failed to record sync state: %s", errors)
        else:
            logger.info(
                "Recorded sync: %s | date=%s | records=%d | status=%s",
                table_name,
                sync_date,
                records,
                status,
            )
