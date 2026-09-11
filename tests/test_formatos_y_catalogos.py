"""Tests for the ISCAM additions: formato CSV, chunk catalogs, schema growth."""

from __future__ import annotations

import os
import sys
from datetime import date
from unittest.mock import MagicMock, call, patch

import pytest
from google.cloud import bigquery
from google.cloud.bigquery import SchemaField

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from bq_loader import BigQueryLoader
from pipeline import Pipeline, PipelineError, load_formatos_venta
from schemas import (
    ALL_TABLES,
    CATALOGOS_AUX,
    DIM_ARTICULO_CLAVES,
    DIM_FORMATO_VENTA,
    FACT_VENTAS_ARTICULO,
    FETCH_CHUNK,
    FETCH_KEYSET,
    TableConfig,
)

TODAY = date(2026, 9, 10)
REPO = os.path.join(os.path.dirname(__file__), "..")


class TestSchemas:
    def test_ventas_has_impuestos_after_utilidad(self):
        names = [f.name for f in FACT_VENTAS_ARTICULO.schema]
        i = names.index("UTILIDAD")
        assert names[i + 1 : i + 3] == ["IMPUESTOS", "IMPORTE_TOTAL"]
        types = {f.name: f.field_type for f in FACT_VENTAS_ARTICULO.schema}
        assert types["IMPUESTOS"] == "NUMERIC" and types["IMPORTE_TOTAL"] == "NUMERIC"

    def test_dim_articulo_claves(self):
        assert DIM_ARTICULO_CLAVES.bq_table == "dim_articulo_claves"
        assert DIM_ARTICULO_CLAVES.endpoint == "/etl/claves-articulos"
        assert DIM_ARTICULO_CLAVES.fetch == FETCH_KEYSET
        names = [f.name for f in DIM_ARTICULO_CLAVES.schema]
        assert names == ["CLAVE_ARTICULO_ID", "ARTICULO_ID", "ROL_CLAVE_ART_ID", "ROL", "ES_PPAL",
                         "ES_GTIN", "CLAVE_ARTICULO", "CONTENIDO_EMPAQUE", "EMPRESA", "_synced_at"]
        required = {f.name for f in DIM_ARTICULO_CLAVES.schema if f.mode == "REQUIRED"}
        assert {"CLAVE_ARTICULO_ID", "ARTICULO_ID"} <= required

    def test_catalogos_aux(self):
        assert [c.bq_table for c in CATALOGOS_AUX] == [
            "dim_tipos_clientes", "dim_zonas_clientes", "dim_sucursales", "dim_precios_empresa"
        ]
        for c in CATALOGOS_AUX:
            assert c.endpoint == "/etl/catalogos-aux"
            assert c.fetch == FETCH_CHUNK
            assert c.schema is None
        assert CATALOGOS_AUX[0].api_params == {"tabla": "tipos_clientes"}
        assert CATALOGOS_AUX[2].api_params == {"tabla": "sucursales"}

    def test_dim_formato_venta_schema(self):
        assert DIM_FORMATO_VENTA.bq_table == "dim_formato_venta"
        types = {f.name: f.field_type for f in DIM_FORMATO_VENTA.schema}
        assert types == {"ORDEN": "INT64", "PATRON": "STRING", "FORMATO": "STRING",
                         "INCLUIR": "BOOL", "EMPRESA": "STRING", "_synced_at": "TIMESTAMP"}

    def test_new_tables_registered(self):
        names = {t.bq_table for t in ALL_TABLES}
        assert {"dim_articulo_claves", "dim_formato_venta", "dim_tipos_clientes"} <= names


class TestFormatosCsv:
    def test_default_csv_parses(self):
        rows = load_formatos_venta(os.path.join(REPO, "config", "formatos_venta.csv"))
        assert [r["ORDEN"] for r in rows] == [10, 20, 30, 40]
        assert rows[0]["PATRON"] == r"^Z-1\s*MOSTRADOR"
        assert rows[0]["FORMATO"] == "Tienda / Cash&Carry"
        assert rows[2]["FORMATO"] == "Excluir" and rows[2]["INCLUIR"] is False
        assert all(isinstance(r["INCLUIR"], bool) for r in rows)
        assert [r["INCLUIR"] for r in rows] == [True, True, False, True]

    def test_relative_path_resolves_from_repo(self):
        assert load_formatos_venta("config/formatos_venta.csv")

    def test_sorted_by_orden_and_bool_variants(self, tmp_path):
        p = tmp_path / "f.csv"
        p.write_text("ORDEN,PATRON,FORMATO,INCLUIR\n20,B,Dos,no\n10,A,Uno,Si\n,,,\n", encoding="utf-8")
        rows = load_formatos_venta(p)
        assert [(r["ORDEN"], r["INCLUIR"]) for r in rows] == [(10, True), (20, False)]

    def test_invalid_regex_rejected(self, tmp_path):
        p = tmp_path / "f.csv"
        p.write_text("ORDEN,PATRON,FORMATO,INCLUIR\n10,(abc,X,true\n", encoding="utf-8")
        with pytest.raises(ValueError, match="invalid PATRON"):
            load_formatos_venta(p)

    def test_missing_header_rejected(self, tmp_path):
        p = tmp_path / "f.csv"
        p.write_text("ORDEN,PATRON\n10,x\n", encoding="utf-8")
        with pytest.raises(ValueError, match="header"):
            load_formatos_venta(p)

    def test_bad_bool_rejected(self, tmp_path):
        p = tmp_path / "f.csv"
        p.write_text("ORDEN,PATRON,FORMATO,INCLUIR\n10,x,X,maybe\n", encoding="utf-8")
        with pytest.raises(ValueError, match="not a boolean"):
            load_formatos_venta(p)


@pytest.fixture
def parts(tmp_path):
    api = MagicMock()
    loader = MagicMock()
    state = MagicMock()
    state.get_last_sync_date.return_value = None
    csv_path = tmp_path / "formatos.csv"
    csv_path.write_text(
        "ORDEN,PATRON,FORMATO,INCLUIR\n10,^Z-1,Tienda,true\n30,COBRANZA,Excluir,false\n",
        encoding="utf-8",
    )
    pipeline = Pipeline(
        api, loader, state, empresa="ALMACENES PACHECO", backfill_start=date(2023, 9, 10),
        today=TODAY, formatos_venta_csv=csv_path,
    )
    return api, loader, state, pipeline, csv_path


class TestPipelineCatalogs:
    def test_chunk_catalog_uses_fetch_chunk_with_tabla(self, parts):
        api, loader, state, p, _ = parts
        api.fetch_chunk.return_value = [{"TIPO_CLIENTE_ID": 1, "NOMBRE": "Z-1  MOSTRADOR SUSANA"}]
        p._sync_full_refresh(CATALOGOS_AUX[0])
        api.fetch_chunk.assert_called_once_with("/etl/catalogos-aux", {"tabla": "tipos_clientes"})
        api.fetch_all.assert_not_called()
        table, rows, schema = loader.load_full_refresh.call_args.args
        assert table == "dim_tipos_clientes" and schema is None
        assert rows[0]["EMPRESA"] == "ALMACENES PACHECO" and rows[0]["NOMBRE"] == "Z-1  MOSTRADOR SUSANA"
        state.record_sync.assert_called_once_with("dim_tipos_clientes", TODAY, 1, "success")

    def test_keyset_catalog_coerces_to_schema(self, parts):
        api, loader, state, p, _ = parts
        api.fetch_keyset.return_value = [
            {"CLAVE_ARTICULO_ID": 5, "ARTICULO_ID": 9, "ROL": "Clave alterna", "ES_PPAL": "N",
             "ES_GTIN": "S", "CLAVE_ARTICULO": "7501234567890  ", "EXTRA": 1}
        ]
        p._sync_full_refresh(DIM_ARTICULO_CLAVES)
        api.fetch_keyset.assert_called_once_with("/etl/claves-articulos", None)
        table, rows, schema = loader.load_full_refresh.call_args.args
        assert table == "dim_articulo_claves" and schema is DIM_ARTICULO_CLAVES.schema
        assert rows[0]["CLAVE_ARTICULO"] == "7501234567890"
        assert "EXTRA" not in rows[0]

    def test_formatos_venta_loaded_from_csv(self, parts):
        api, loader, state, p, _ = parts
        p._sync_formatos_venta()
        table, rows, schema = loader.load_full_refresh.call_args.args
        assert table == "dim_formato_venta" and schema is DIM_FORMATO_VENTA.schema
        assert [r["ORDEN"] for r in rows] == [10, 30]
        assert rows[1]["INCLUIR"] is False
        assert rows[0]["EMPRESA"] == "ALMACENES PACHECO" and rows[0]["_synced_at"]
        assert set(rows[0]) == {"ORDEN", "PATRON", "FORMATO", "INCLUIR", "EMPRESA", "_synced_at"}
        state.record_sync.assert_called_with("dim_formato_venta", TODAY, 2, "success")

    def test_sync_catalogs_covers_every_dim(self, parts):
        api, loader, state, p, _ = parts
        api.fetch_all.return_value = [{"X": 1}]
        api.fetch_keyset.return_value = [{"CLAVE_ARTICULO_ID": 1, "ARTICULO_ID": 1,
                                         "PRECIO_COMPRA_ID": 1, "PROVEEDOR_ID": 1}]
        api.fetch_chunk.return_value = [{"ID": 1}]
        p.sync_catalogs()
        loaded = [c.args[0] for c in loader.load_full_refresh.call_args_list]
        assert loaded == [
            "dim_clientes", "dim_articulos", "dim_proveedores", "dim_almacenes", "dim_vendedores",
            "dim_lineas", "dim_tipos_clientes", "dim_zonas_clientes", "dim_sucursales",
            "dim_precios_empresa", "dim_articulo_proveedor", "dim_articulo_claves", "dim_formato_venta",
        ]
        assert api.fetch_chunk.call_args_list == [
            call("/etl/catalogos-aux", {"tabla": "tipos_clientes"}),
            call("/etl/catalogos-aux", {"tabla": "zonas_clientes"}),
            call("/etl/catalogos-aux", {"tabla": "sucursales"}),
            call("/etl/catalogos-aux", {"tabla": "precios_empresa"}),
        ]
        assert p.failures == []

    def test_missing_csv_is_recoverable(self, parts, tmp_path):
        api, loader, state, p, _ = parts
        p.formatos_venta_csv = tmp_path / "nope.csv"
        api.fetch_all.return_value = []
        api.fetch_keyset.return_value = []
        api.fetch_chunk.return_value = []
        p.sync_catalogs()
        assert p.failures == ["dim_formato_venta"]
        with pytest.raises(PipelineError, match="dim_formato_venta"):
            p._raise_if_failed()
        error_calls = [c for c in state.record_sync.call_args_list if c.args[3] == "error"]
        assert error_calls[0].args[0] == "dim_formato_venta"

    def test_ensure_tables_includes_new_dims(self, parts):
        api, loader, state, p, _ = parts
        p.ensure_tables()
        ensured = [c.args[0].bq_table for c in loader.ensure_table.call_args_list]
        assert "dim_articulo_claves" in ensured and "dim_formato_venta" in ensured
        assert "fact_ventas_articulo" in ensured

    def test_nightly_default_csv_exists(self):
        api, loader, state = MagicMock(), MagicMock(), MagicMock()
        state.get_last_sync_date.return_value = None
        p = Pipeline(api, loader, state, empresa="X", backfill_start=date(2023, 1, 1), today=TODAY)
        assert p.formatos_venta_csv.is_file()


@pytest.fixture
def bq():
    with patch("bq_loader.bigquery.Client") as mock_cls:
        client = MagicMock()
        mock_cls.return_value = client
        yield BigQueryLoader("proj", "ds", "us-central1"), client


class TestEnsureTableAddsColumns:
    def test_adds_missing_columns_nullable(self, bq, caplog):
        ld, client = bq
        old_schema = [f for f in FACT_VENTAS_ARTICULO.schema if f.name not in ("IMPUESTOS", "IMPORTE_TOTAL")]
        existing = bigquery.Table("proj.ds.fact_ventas_articulo", schema=old_schema)
        existing.time_partitioning = bigquery.TimePartitioning(field="FECHA", expiration_ms=1100 * 86400000)
        client.get_table.return_value = existing
        cfg = TableConfig(**{**FACT_VENTAS_ARTICULO.__dict__, "expiration_days": 1100})

        with caplog.at_level("INFO", logger="bq_loader"):
            ld.ensure_table(cfg)

        client.create_table.assert_not_called()
        client.update_table.assert_called_once()
        updated, fields = client.update_table.call_args.args
        assert fields == ["schema"]
        names = [f.name for f in updated.schema]
        assert names[-2:] == ["IMPUESTOS", "IMPORTE_TOTAL"]
        assert len(names) == len(FACT_VENTAS_ARTICULO.schema)
        added = {f.name: f for f in updated.schema if f.name in ("IMPUESTOS", "IMPORTE_TOTAL")}
        assert all(f.mode == "NULLABLE" and f.field_type == "NUMERIC" for f in added.values())
        assert "IMPUESTOS, IMPORTE_TOTAL" in caplog.text

    def test_required_in_config_becomes_nullable_when_added(self, bq):
        ld, client = bq
        existing = bigquery.Table("proj.ds.t", schema=[SchemaField("ID", "INT64", mode="REQUIRED")])
        client.get_table.return_value = existing
        cfg = TableConfig(bq_table="t", endpoint="/x", schema=[
            SchemaField("ID", "INT64", mode="REQUIRED"),
            SchemaField("NUEVA", "STRING", mode="REQUIRED"),
        ])
        ld.ensure_table(cfg)
        updated, fields = client.update_table.call_args.args
        assert fields == ["schema"]
        assert [(f.name, f.mode) for f in updated.schema] == [("ID", "REQUIRED"), ("NUEVA", "NULLABLE")]

    def test_no_update_when_schema_matches(self, bq):
        ld, client = bq
        existing = bigquery.Table("proj.ds.dim_formato_venta", schema=DIM_FORMATO_VENTA.schema)
        client.get_table.return_value = existing
        ld.ensure_table(DIM_FORMATO_VENTA)
        client.update_table.assert_not_called()
        client.create_table.assert_not_called()

    def test_updates_both_expiration_and_schema(self, bq):
        ld, client = bq
        existing = bigquery.Table("proj.ds.fact_ventas_articulo",
                                  schema=FACT_VENTAS_ARTICULO.schema[:-3])
        existing.time_partitioning = bigquery.TimePartitioning(field="FECHA", expiration_ms=None)
        client.get_table.return_value = existing
        cfg = TableConfig(**{**FACT_VENTAS_ARTICULO.__dict__, "expiration_days": 1100})
        ld.ensure_table(cfg)
        assert [c.args[1] for c in client.update_table.call_args_list] == [["time_partitioning"], ["schema"]]

    def test_creates_dim_claves_with_clustering(self, bq):
        from google.api_core.exceptions import NotFound

        ld, client = bq
        client.get_table.side_effect = NotFound("nope")
        ld.ensure_table(DIM_ARTICULO_CLAVES)
        table = client.create_table.call_args.args[0]
        assert table.time_partitioning is None
        assert table.clustering_fields == ["ARTICULO_ID"]
        assert len(table.schema) == len(DIM_ARTICULO_CLAVES.schema)
