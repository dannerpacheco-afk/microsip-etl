"""BigQuery loader: handles dataset/table creation and data loading."""

from __future__ import annotations

import json
import logging
import re
import tempfile
import uuid
from datetime import date
from pathlib import Path

from google.api_core.exceptions import NotFound
from google.cloud import bigquery
from google.cloud.bigquery import SchemaField

from schemas import TableConfig

logger = logging.getLogger(__name__)

# Explicit schema for the sync state metadata table
SYNC_STATE_SCHEMA = [
    SchemaField("table_name", "STRING", mode="REQUIRED"),
    SchemaField("last_sync_date", "DATE", mode="REQUIRED"),
    SchemaField("last_run_at", "TIMESTAMP", mode="REQUIRED"),
    SchemaField("records_synced", "INTEGER", mode="REQUIRED"),
    SchemaField("status", "STRING", mode="REQUIRED"),
]

# Regex for valid BigQuery table/dataset identifiers (prevent injection)
_VALID_BQ_IDENTIFIER = re.compile(r"^[a-zA-Z0-9_]+$")

_MS_PER_DAY = 24 * 60 * 60 * 1000


def _validate_identifier(name: str, label: str = "identifier") -> str:
    """Validate that a BigQuery identifier contains only safe characters."""
    if not _VALID_BQ_IDENTIFIER.match(name):
        raise ValueError(
            f"Invalid BigQuery {label}: {name!r}. "
            "Only alphanumeric characters and underscores are allowed."
        )
    return name


class BigQueryLoader:
    """Manages BigQuery dataset, tables, and data loading.

    Implements the context manager protocol so the underlying BigQuery
    client is properly closed when the loader goes out of scope.
    """

    def __init__(self, project_id: str, dataset: str, location: str):
        self.project_id = project_id
        self.dataset = _validate_identifier(dataset, "dataset")
        self.location = location
        self.client = bigquery.Client(project=project_id)
        self.dataset_ref = f"{project_id}.{dataset}"

    # --- Context manager ---

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        """Close the underlying BigQuery client."""
        self.client.close()

    # --- Setup ---

    def ensure_dataset(self, dataset: str | None = None):
        """Create a dataset if it doesn't exist (defaults to the main one)."""
        name = _validate_identifier(dataset or self.dataset, "dataset")
        ds = bigquery.Dataset(f"{self.project_id}.{name}")
        ds.location = self.location
        self.client.create_dataset(ds, exists_ok=True)
        logger.info("Dataset %s.%s ready", self.project_id, name)

    def ensure_sync_state_table(self):
        """Create the _etl_sync_state table if it doesn't exist."""
        table_id = f"{self.dataset_ref}._etl_sync_state"
        table = bigquery.Table(table_id, schema=SYNC_STATE_SCHEMA)
        self.client.create_table(table, exists_ok=True)
        logger.info("Sync state table ready")

    def ensure_table(self, config: TableConfig):
        """Create a table with explicit schema, partitioning and retention.

        If the table already exists, the partition expiration is reconciled
        and columns present in ``config.schema`` but missing from the live
        table are added as NULLABLE (BigQuery only allows additive changes;
        type changes and drops are left to a manual migration).
        """
        if config.schema is None:
            return  # autodetect tables are created by the first load
        table_id = f"{self.dataset_ref}.{_validate_identifier(config.bq_table, 'table_name')}"
        partitioning = self._partitioning(config)

        try:
            existing = self.client.get_table(table_id)
        except NotFound:
            table = bigquery.Table(table_id, schema=config.schema)
            table.time_partitioning = partitioning
            if config.clustering_fields:
                for cf in config.clustering_fields:
                    _validate_identifier(cf, "clustering_field")
                table.clustering_fields = config.clustering_fields
            self.client.create_table(table)
            logger.info("Created table %s", table_id)
            return

        wanted_ms = partitioning.expiration_ms if partitioning else None
        current_ms = (
            existing.time_partitioning.expiration_ms
            if existing.time_partitioning
            else None
        )
        if existing.time_partitioning and wanted_ms != current_ms:
            existing.time_partitioning = bigquery.TimePartitioning(
                type_=existing.time_partitioning.type_,
                field=existing.time_partitioning.field,
                expiration_ms=wanted_ms,
            )
            self.client.update_table(existing, ["time_partitioning"])
            logger.info(
                "Updated partition expiration on %s: %s -> %s ms",
                table_id,
                current_ms,
                wanted_ms,
            )

        self._add_missing_columns(existing, config.schema, table_id)

    def _add_missing_columns(
        self, existing: bigquery.Table, wanted: list[SchemaField], table_id: str
    ) -> list[str]:
        """Append schema columns absent from the live table (as NULLABLE)."""
        live = {f.name.upper() for f in existing.schema}
        missing = [f for f in wanted if f.name.upper() not in live]
        if not missing:
            return []
        added = [
            SchemaField(f.name, f.field_type, mode="NULLABLE", description=f.description)
            for f in missing
        ]
        existing.schema = list(existing.schema) + added
        self.client.update_table(existing, ["schema"])
        names = [f.name for f in added]
        logger.info("Added missing columns to %s: %s", table_id, ", ".join(names))
        return names

    def _partitioning(self, config: TableConfig) -> bigquery.TimePartitioning | None:
        if not config.partition_field:
            return None
        _validate_identifier(config.partition_field, "partition_field")
        type_ = (
            bigquery.TimePartitioningType.MONTH
            if config.partition_type.upper() == "MONTH"
            else bigquery.TimePartitioningType.DAY
        )
        expiration_ms = (
            config.expiration_days * _MS_PER_DAY if config.expiration_days else None
        )
        return bigquery.TimePartitioning(
            type_=type_, field=config.partition_field, expiration_ms=expiration_ms
        )

    # --- Load methods ---

    def load_full_refresh(
        self,
        table_name: str,
        rows: list[dict],
        schema: list[SchemaField] | None = None,
    ):
        """Truncate and reload a table. For catalog tables."""
        _validate_identifier(table_name, "table_name")
        if not rows:
            logger.warning("No rows to load for %s, skipping", table_name)
            return
        table_id = f"{self.dataset_ref}.{table_name}"
        self._load_file(
            table_id, rows, schema, bigquery.WriteDisposition.WRITE_TRUNCATE
        )
        logger.info("Loaded %d rows to %s (full refresh)", len(rows), table_name)

    def load_append(
        self,
        table_name: str,
        rows: list[dict],
        schema: list[SchemaField] | None = None,
    ):
        """Append rows to a table."""
        _validate_identifier(table_name, "table_name")
        if not rows:
            logger.warning("No rows to append for %s, skipping", table_name)
            return
        table_id = f"{self.dataset_ref}.{table_name}"
        self._load_file(table_id, rows, schema, bigquery.WriteDisposition.WRITE_APPEND)
        logger.info("Appended %d rows to %s", len(rows), table_name)

    def load_partition(
        self,
        table_name: str,
        rows: list[dict],
        schema: list[SchemaField],
        partition_date: date,
    ):
        """Replace a single DAY partition (idempotent daily snapshots).

        Uses the ``table$YYYYMMDD`` partition decorator with WRITE_TRUNCATE,
        so running twice on the same day never duplicates rows.
        """
        _validate_identifier(table_name, "table_name")
        if not rows:
            logger.warning("No rows for snapshot %s, skipping", table_name)
            return
        table_id = f"{self.dataset_ref}.{table_name}${partition_date:%Y%m%d}"
        self._load_file(table_id, rows, schema, bigquery.WriteDisposition.WRITE_TRUNCATE)
        logger.info(
            "Loaded %d rows to %s partition %s (snapshot)",
            len(rows),
            table_name,
            partition_date,
        )

    def load_replace_range(
        self,
        table_name: str,
        rows: list[dict],
        schema: list[SchemaField],
        date_field: str,
        start: date,
        end: date,
    ):
        """Replace every row whose ``date_field`` falls in [start, end].

        DELETE + INSERT inside one transaction. Rows that disappeared at the
        source (cancelled documents) are removed; an empty chunk still clears
        the range.
        """
        _validate_identifier(date_field, "date_field")
        where = f"{date_field} BETWEEN @range_start AND @range_end"
        params = [
            bigquery.ScalarQueryParameter("range_start", "DATE", start),
            bigquery.ScalarQueryParameter("range_end", "DATE", end),
        ]
        self._replace_where(table_name, rows, schema, where, params)
        logger.info(
            "Replaced %s %s..%s with %d rows", table_name, start, end, len(rows)
        )

    def load_replace_months(
        self,
        table_name: str,
        rows: list[dict],
        schema: list[SchemaField],
        period_field: str,
        periods: list[date],
    ):
        """Replace every row whose ``period_field`` is one of ``periods``."""
        _validate_identifier(period_field, "period_field")
        where = f"{period_field} IN UNNEST(@periods)"
        params = [bigquery.ArrayQueryParameter("periods", "DATE", periods)]
        self._replace_where(table_name, rows, schema, where, params)
        logger.info(
            "Replaced %s periods %s with %d rows",
            table_name,
            [p.isoformat() for p in periods],
            len(rows),
        )

    def load_incremental(
        self,
        table_name: str,
        rows: list[dict],
        primary_key: str,
        partition_field: str = "",
        clustering_fields: list[str] | None = None,
        schema: list[SchemaField] | None = None,
    ):
        """Upsert rows via staging table + MERGE. For transaction tables."""
        _validate_identifier(table_name, "table_name")
        _validate_identifier(primary_key, "primary_key")
        if not rows:
            logger.warning("No rows to load for %s, skipping", table_name)
            return

        table_id = f"{self.dataset_ref}.{table_name}"
        staging_table = self._staging_name(table_name)

        try:
            self._load_file(
                staging_table, rows, schema, bigquery.WriteDisposition.WRITE_TRUNCATE
            )
            self._ensure_target_from_staging(
                table_id, staging_table, partition_field, clustering_fields
            )

            columns = self._staging_columns(staging_table)
            update_cols = [c for c in columns if c != primary_key]
            update_clause = ", ".join(f"T.{c} = S.{c}" for c in update_cols)
            insert_cols = ", ".join(columns)
            insert_vals = ", ".join(f"S.{c}" for c in columns)

            merge_sql = f"""
            MERGE `{table_id}` T
            USING `{staging_table}` S
            ON T.{primary_key} = S.{primary_key}
            WHEN MATCHED THEN
                UPDATE SET {update_clause}
            WHEN NOT MATCHED THEN
                INSERT ({insert_cols}) VALUES ({insert_vals})
            """
            self.client.query(merge_sql).result()
            logger.info("Merged %d rows into %s (incremental)", len(rows), table_name)
        finally:
            self.client.delete_table(staging_table, not_found_ok=True)

    # --- SQL helpers ---

    def execute(self, sql: str, params: list | None = None):
        """Run a statement (views, DDL) and wait for it."""
        job_config = bigquery.QueryJobConfig(query_parameters=params or [])
        return self.client.query(sql, job_config=job_config).result()

    # --- Internal helpers ---

    def _replace_where(
        self,
        table_name: str,
        rows: list[dict],
        schema: list[SchemaField],
        where: str,
        params: list,
    ):
        _validate_identifier(table_name, "table_name")
        table_id = f"{self.dataset_ref}.{table_name}"
        job_config = bigquery.QueryJobConfig(query_parameters=params)

        if not rows:
            self.client.query(
                f"DELETE FROM `{table_id}` WHERE {where}", job_config=job_config
            ).result()
            return

        staging_table = self._staging_name(table_name)
        try:
            self._load_file(
                staging_table, rows, schema, bigquery.WriteDisposition.WRITE_TRUNCATE
            )
            columns = ", ".join(self._staging_columns(staging_table))
            sql = f"""
            BEGIN TRANSACTION;
            DELETE FROM `{table_id}` WHERE {where};
            INSERT INTO `{table_id}` ({columns})
            SELECT {columns} FROM `{staging_table}`;
            COMMIT TRANSACTION;
            """
            self.client.query(sql, job_config=job_config).result()
        finally:
            self.client.delete_table(staging_table, not_found_ok=True)

    def _staging_name(self, table_name: str) -> str:
        # UUID suffix prevents collisions between concurrent runs
        run_id = uuid.uuid4().hex[:8]
        return f"{self.dataset_ref}._staging_{table_name}_{run_id}"

    def _staging_columns(self, staging_table: str) -> list[str]:
        columns = [f.name for f in self.client.get_table(staging_table).schema]
        for col in columns:
            _validate_identifier(col, "column_name")
        return columns

    def _load_file(
        self,
        table_id: str,
        rows: list[dict],
        schema: list[SchemaField] | None,
        disposition: str,
    ):
        """Load rows from a temp NDJSON file into ``table_id``."""
        ndjson = self._rows_to_ndjson_file(rows)
        try:
            if schema:
                job_config = bigquery.LoadJobConfig(
                    source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
                    write_disposition=disposition,
                    schema=schema,
                    ignore_unknown_values=True,
                )
            else:
                job_config = bigquery.LoadJobConfig(
                    source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
                    write_disposition=disposition,
                    autodetect=True,
                )
            with open(ndjson, "rb") as f:
                job = self.client.load_table_from_file(f, table_id, job_config=job_config)
            job.result()
        finally:
            Path(ndjson).unlink(missing_ok=True)

    def _ensure_target_from_staging(
        self,
        table_id: str,
        staging_table: str,
        partition_field: str,
        clustering_fields: list[str] | None,
    ):
        """Create target table from staging schema if it doesn't exist."""
        try:
            self.client.get_table(table_id)
            return  # already exists
        except NotFound:
            pass  # table doesn't exist, create it below

        staging = self.client.get_table(staging_table)
        table = bigquery.Table(table_id, schema=staging.schema)

        if partition_field:
            _validate_identifier(partition_field, "partition_field")
            table.time_partitioning = bigquery.TimePartitioning(
                type_=bigquery.TimePartitioningType.DAY,
                field=partition_field,
            )
        if clustering_fields:
            for cf in clustering_fields:
                _validate_identifier(cf, "clustering_field")
            table.clustering_fields = clustering_fields

        self.client.create_table(table)
        logger.info("Created target table %s", table_id)

    def _rows_to_ndjson_file(self, rows: list[dict]) -> str:
        """Write rows to a temporary NDJSON file. Returns file path."""
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".ndjson", delete=False, encoding="utf-8"
        )
        try:
            for row in rows:
                tmp.write(json.dumps(row, default=str) + "\n")
        finally:
            tmp.close()
        return tmp.name
