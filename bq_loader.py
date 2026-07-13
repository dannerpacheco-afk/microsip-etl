"""BigQuery loader: handles dataset/table creation and data loading."""

from __future__ import annotations

import json
import logging
import re
import tempfile
import uuid
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from google.api_core.exceptions import NotFound
from google.cloud import bigquery
from google.cloud.bigquery import SchemaField

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

# Pairs of types that can be promoted to a wider common type.
# Anything not listed promotes to STRING (safe catch-all).
_TYPE_PROMOTION = {
    frozenset(["BOOLEAN", "INTEGER"]): "INTEGER",
    frozenset(["INTEGER", "FLOAT"]): "FLOAT",
    frozenset(["DATE", "TIMESTAMP"]): "TIMESTAMP",
}


def _validate_identifier(name: str, label: str = "identifier") -> str:
    """Validate that a BigQuery identifier contains only safe characters."""
    if not _VALID_BQ_IDENTIFIER.match(name):
        raise ValueError(
            f"Invalid BigQuery {label}: {name!r}. "
            "Only alphanumeric characters and underscores are allowed."
        )
    return name


def _detect_value_type(value) -> str | None:
    """Return BigQuery type string for a Python value, or None if null."""
    if value is None:
        return None
    # Check bool BEFORE int — in Python, bool is a subclass of int
    if isinstance(value, bool):
        return "BOOLEAN"
    if isinstance(value, int):
        return "INTEGER"
    if isinstance(value, (float, Decimal)):
        return "FLOAT"
    # Check datetime BEFORE date — datetime is a subclass of date
    if isinstance(value, datetime):
        return "TIMESTAMP"
    if isinstance(value, date):
        return "DATE"
    # str, bytes, or anything else → STRING
    return "STRING"


def _promote_type(a: str, b: str) -> str:
    """Return the widest compatible type between two observed types."""
    if a == b:
        return a
    return _TYPE_PROMOTION.get(frozenset([a, b]), "STRING")


def infer_schema(rows: list[dict]) -> list[SchemaField]:
    """Infer a BigQuery schema by scanning ALL rows in the batch.

    More reliable than BigQuery's autodetect because:
      - Autodetect only samples the first ~100 rows, so rare values
        (like a decimal buried in an otherwise-integer column) cause
        late failures during load. This scans every row.
      - Handles Python Decimal → FLOAT, which json.dumps would
        otherwise serialize as a string and confuse autodetect.
      - Promotes int/float correctly and falls back to STRING for
        genuinely mixed types (e.g. '5100' and '5100.2' are both
        strings, so the column is STRING — correct for account codes).

    All fields are NULLABLE by default (Microsip data has many nulls).
    """
    types: dict[str, str | None] = {}
    for row in rows:
        for key, value in row.items():
            observed = _detect_value_type(value)
            if observed is None:
                types.setdefault(key, None)
                continue
            current = types.get(key)
            types[key] = observed if current is None else _promote_type(
                current, observed
            )

    # Columns that were all-null default to STRING (safe default)
    return [
        SchemaField(name, bq_type or "STRING")
        for name, bq_type in types.items()
    ]


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

    def ensure_dataset(self):
        """Create dataset if it doesn't exist."""
        ds = bigquery.Dataset(self.dataset_ref)
        ds.location = self.location
        self.client.create_dataset(ds, exists_ok=True)
        logger.info("Dataset %s ready", self.dataset_ref)

    def ensure_sync_state_table(self):
        """Create the _etl_sync_state table if it doesn't exist."""
        table_id = f"{self.dataset_ref}._etl_sync_state"
        table = bigquery.Table(table_id, schema=SYNC_STATE_SCHEMA)
        self.client.create_table(table, exists_ok=True)
        logger.info("Sync state table ready")

    # --- Load methods ---

    def load_full_refresh(
        self,
        table_name: str,
        rows: list[dict],
        schema: list[SchemaField] | None = None,
    ):
        """Truncate and reload a table. For catalog tables.

        Args:
            table_name: Target BigQuery table name.
            rows: List of dicts to load.
            schema: Optional explicit schema. If None, schema is inferred
                from the rows (scans all rows, not just a sample).
        """
        _validate_identifier(table_name, "table_name")
        if not rows:
            logger.warning("No rows to load for %s, skipping", table_name)
            return

        table_id = f"{self.dataset_ref}.{table_name}"
        effective_schema = schema or infer_schema(rows)
        ndjson = self._rows_to_ndjson_file(rows)

        try:
            job_config = bigquery.LoadJobConfig(
                source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
                write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
                schema=effective_schema,
            )

            with open(ndjson, "rb") as f:
                job = self.client.load_table_from_file(
                    f, table_id, job_config=job_config
                )
            job.result()  # wait for completion
            logger.info(
                "Loaded %d rows to %s (full refresh)", len(rows), table_name
            )
        finally:
            Path(ndjson).unlink(missing_ok=True)

    def load_append(
        self,
        table_name: str,
        rows: list[dict],
        schema: list[SchemaField] | None = None,
    ):
        """Append rows to a table. For snapshot tables that accumulate history.

        Args:
            table_name: Target BigQuery table name.
            rows: List of dicts to append.
            schema: Optional explicit schema. If None, schema is inferred
                from the rows.
        """
        _validate_identifier(table_name, "table_name")
        if not rows:
            logger.warning("No rows to append for %s, skipping", table_name)
            return

        table_id = f"{self.dataset_ref}.{table_name}"
        effective_schema = schema or infer_schema(rows)
        ndjson = self._rows_to_ndjson_file(rows)

        try:
            job_config = bigquery.LoadJobConfig(
                source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
                write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
                schema=effective_schema,
                # New columns can appear between runs (Microsip upgrades)
                schema_update_options=[
                    bigquery.SchemaUpdateOption.ALLOW_FIELD_ADDITION
                ],
            )

            with open(ndjson, "rb") as f:
                job = self.client.load_table_from_file(
                    f, table_id, job_config=job_config
                )
            job.result()
            logger.info(
                "Appended %d rows to %s (snapshot)", len(rows), table_name
            )
        finally:
            Path(ndjson).unlink(missing_ok=True)

    def load_incremental(
        self,
        table_name: str,
        rows: list[dict],
        primary_key: str,
        partition_field: str = "",
        clustering_fields: list[str] | None = None,
    ):
        """Upsert rows via staging table + MERGE. For transaction tables."""
        _validate_identifier(table_name, "table_name")
        _validate_identifier(primary_key, "primary_key")
        if not rows:
            logger.warning("No rows to load for %s, skipping", table_name)
            return

        table_id = f"{self.dataset_ref}.{table_name}"
        # Use UUID suffix to prevent race conditions with concurrent runs
        run_id = uuid.uuid4().hex[:8]
        staging_table = f"{self.dataset_ref}._staging_{table_name}_{run_id}"

        staging_schema = infer_schema(rows)
        ndjson = self._rows_to_ndjson_file(rows)

        try:
            # Step 1: Load into staging table (always truncate staging)
            job_config = bigquery.LoadJobConfig(
                source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
                write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
                schema=staging_schema,
            )
            with open(ndjson, "rb") as f:
                job = self.client.load_table_from_file(
                    f, staging_table, job_config=job_config
                )
            job.result()

            # Step 2: Ensure target table exists (create from staging if not)
            self._ensure_target_from_staging(
                table_id, staging_table, partition_field, clustering_fields
            )

            # Step 3: MERGE staging into target
            columns = [
                field.name
                for field in self.client.get_table(staging_table).schema
            ]
            # Validate all column names to prevent SQL injection
            for col in columns:
                _validate_identifier(col, "column_name")

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

            job = self.client.query(merge_sql)
            job.result()

            logger.info(
                "Merged %d rows into %s (incremental)", len(rows), table_name
            )
        finally:
            # Always clean up temp files and staging table
            Path(ndjson).unlink(missing_ok=True)
            self.client.delete_table(staging_table, not_found_ok=True)

    # --- Internal helpers ---

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
