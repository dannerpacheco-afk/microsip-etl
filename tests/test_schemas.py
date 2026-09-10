"""Tests for schemas.py — table configs and row coercion."""

from __future__ import annotations

import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from google.cloud.bigquery import SchemaField

from schemas import (
    ALL_TABLES,
    FACT_COMPRAS_PARTIDAS,
    FACT_SALDOS_MENSUALES,
    FACT_VENTAS_ARTICULO,
    INVENTARIO_EXISTENCIAS,
    coerce_row,
)


class TestCoerceRow:
    schema = [
        SchemaField("ID", "INT64", mode="REQUIRED"),
        SchemaField("IMPORTE", "NUMERIC"),
        SchemaField("FECHA", "DATE"),
        SchemaField("MODIF", "DATETIME"),
        SchemaField("_synced_at", "TIMESTAMP"),
    ]

    def test_drops_unknown_and_keeps_numeric_strings(self):
        row = {"ID": 1, "IMPORTE": "123.4500", "EXTRA": "x"}
        out = coerce_row(row, self.schema)
        assert out == {
            "ID": 1,
            "IMPORTE": "123.4500",
            "FECHA": None,
            "MODIF": None,
            "_synced_at": None,
        }

    def test_empty_string_becomes_null(self):
        assert coerce_row({"ID": 1, "IMPORTE": ""}, self.schema)["IMPORTE"] is None

    def test_datetime_uses_space_separator(self):
        out = coerce_row({"ID": 1, "MODIF": "2025-03-03T10:20:30"}, self.schema)
        assert out["MODIF"] == "2025-03-03 10:20:30"
        out = coerce_row({"ID": 1, "MODIF": datetime(2025, 3, 3, 10, 20, 30)}, self.schema)
        assert out["MODIF"] == "2025-03-03 10:20:30"

    def test_timestamp_string_untouched(self):
        ts = "2025-03-03T10:20:30+00:00"
        assert coerce_row({"ID": 1, "_synced_at": ts}, self.schema)["_synced_at"] == ts

    def test_date_objects_become_iso(self):
        assert coerce_row({"ID": 1, "FECHA": date(2025, 3, 3)}, self.schema)["FECHA"] == "2025-03-03"


class TestTableConfigs:
    def test_unique_table_names(self):
        names = [t.bq_table for t in ALL_TABLES]
        assert len(names) == len(set(names))

    def test_explicit_schemas_include_meta_and_partition_field(self):
        for cfg in ALL_TABLES:
            if cfg.schema is None:
                continue
            fields = {f.name for f in cfg.schema}
            assert {"EMPRESA", "_synced_at"} <= fields, cfg.bq_table
            if cfg.partition_field:
                assert cfg.partition_field in fields, cfg.bq_table
            for cf in cfg.clustering_fields:
                assert cf in fields, cfg.bq_table

    def test_fact_strategies(self):
        assert FACT_VENTAS_ARTICULO.fetch == "chunk"
        assert FACT_VENTAS_ARTICULO.load == "replace_range"
        assert FACT_COMPRAS_PARTIDAS.fetch == "keyset"
        assert FACT_SALDOS_MENSUALES.partition_type == "MONTH"
        assert FACT_SALDOS_MENSUALES.expiration_days is None
        assert INVENTARIO_EXISTENCIAS.load == "snapshot"
        assert INVENTARIO_EXISTENCIAS.partition_field == "_snapshot_date"
