"""Tests for bq_loader.py — table creation, range replace, partition loads."""

from __future__ import annotations

import os
import sys
from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from google.api_core.exceptions import NotFound
from google.cloud import bigquery
from google.cloud.bigquery import SchemaField

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from bq_loader import BigQueryLoader
from schemas import FACT_SALDOS_MENSUALES, FACT_VENTAS_ARTICULO, TableConfig

SCHEMA = [
    SchemaField("ID", "INT64", mode="REQUIRED"),
    SchemaField("FECHA", "DATE"),
    SchemaField("EMPRESA", "STRING"),
]


@pytest.fixture
def loader():
    with patch("bq_loader.bigquery.Client") as mock_cls:
        client = MagicMock()
        mock_cls.return_value = client
        ld = BigQueryLoader("proj", "ds", "us-central1")
        staging = MagicMock()
        staging.schema = SCHEMA
        client.get_table.return_value = staging
        yield ld, client


class TestReplaceRange:
    def test_delete_and_insert_in_transaction(self, loader):
        ld, client = loader
        rows = [{"ID": 1, "FECHA": "2025-01-01", "EMPRESA": "X"}]
        ld.load_replace_range("t", rows, SCHEMA, "FECHA", date(2025, 1, 1), date(2025, 1, 7))

        # staging load with explicit schema
        load_cfg = client.load_table_from_file.call_args.kwargs["job_config"]
        assert load_cfg.schema == SCHEMA
        assert load_cfg.ignore_unknown_values is True
        staging_id = client.load_table_from_file.call_args.args[1]
        assert staging_id.startswith("proj.ds._staging_t_")

        sql = client.query.call_args.args[0]
        assert "BEGIN TRANSACTION" in sql and "COMMIT TRANSACTION" in sql
        assert "DELETE FROM `proj.ds.t` WHERE FECHA BETWEEN @range_start AND @range_end" in sql
        assert "INSERT INTO `proj.ds.t` (ID, FECHA, EMPRESA)" in sql
        params = client.query.call_args.kwargs["job_config"].query_parameters
        assert [p.name for p in params] == ["range_start", "range_end"]
        client.delete_table.assert_called_once_with(staging_id, not_found_ok=True)

    def test_empty_rows_only_deletes(self, loader):
        ld, client = loader
        ld.load_replace_range("t", [], SCHEMA, "FECHA", date(2025, 1, 1), date(2025, 1, 7))
        client.load_table_from_file.assert_not_called()
        sql = client.query.call_args.args[0]
        assert sql.startswith("DELETE FROM `proj.ds.t` WHERE FECHA BETWEEN")

    def test_replace_months_uses_unnest(self, loader):
        ld, client = loader
        ld.load_replace_months("t", [{"ID": 1}], SCHEMA, "PERIODO", [date(2025, 1, 1)])
        sql = client.query.call_args.args[0]
        assert "WHERE PERIODO IN UNNEST(@periods)" in sql

    def test_rejects_bad_identifiers(self, loader):
        ld, _ = loader
        with pytest.raises(ValueError):
            ld.load_replace_range("t", [], SCHEMA, "FECHA; DROP", date(2025, 1, 1), date(2025, 1, 2))


class TestPartition:
    def test_partition_decorator_truncate(self, loader):
        ld, client = loader
        ld.load_partition("snap", [{"ID": 1}], SCHEMA, date(2026, 9, 10))
        table_id = client.load_table_from_file.call_args.args[1]
        assert table_id == "proj.ds.snap$20260910"
        cfg = client.load_table_from_file.call_args.kwargs["job_config"]
        assert cfg.write_disposition == bigquery.WriteDisposition.WRITE_TRUNCATE


class TestEnsureTable:
    def test_creates_with_partitioning_and_expiration(self, loader):
        ld, client = loader
        client.get_table.side_effect = NotFound("nope")
        cfg = TableConfig(**{**FACT_VENTAS_ARTICULO.__dict__, "expiration_days": 1100})
        ld.ensure_table(cfg)
        table = client.create_table.call_args.args[0]
        assert table.time_partitioning.field == "FECHA"
        assert table.time_partitioning.type_ == "DAY"
        assert table.time_partitioning.expiration_ms == 1100 * 86400000
        assert table.clustering_fields == ["ARTICULO_ID", "ALMACEN_ID"]
        assert len(table.schema) == len(FACT_VENTAS_ARTICULO.schema)

    def test_month_partition_without_expiration(self, loader):
        ld, client = loader
        client.get_table.side_effect = NotFound("nope")
        ld.ensure_table(FACT_SALDOS_MENSUALES)
        table = client.create_table.call_args.args[0]
        assert table.time_partitioning.type_ == "MONTH"
        assert table.time_partitioning.expiration_ms is None

    def test_updates_expiration_when_different(self, loader):
        ld, client = loader
        existing = bigquery.Table("proj.ds.fact_ventas_articulo", schema=FACT_VENTAS_ARTICULO.schema)
        existing.time_partitioning = bigquery.TimePartitioning(field="FECHA", expiration_ms=None)
        client.get_table.side_effect = None
        client.get_table.return_value = existing
        cfg = TableConfig(**{**FACT_VENTAS_ARTICULO.__dict__, "expiration_days": 1100})
        ld.ensure_table(cfg)
        client.update_table.assert_called_once()
        updated, fields = client.update_table.call_args.args
        assert fields == ["time_partitioning"]
        assert updated.time_partitioning.expiration_ms == 1100 * 86400000

    def test_autodetect_tables_skipped(self, loader):
        ld, client = loader
        ld.ensure_table(TableConfig(bq_table="dim_x", endpoint="/x"))
        client.get_table.assert_not_called()
        client.create_table.assert_not_called()
