"""Tests for pipeline.py — chunking, backfill resume, nightly orchestration."""

from __future__ import annotations

import os
import sys
from datetime import date
from unittest.mock import MagicMock, call

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pipeline import (
    Pipeline,
    PipelineError,
    add_months,
    iter_chunks,
    iter_months,
    month_last_day,
)
from schemas import (
    FACT_COMPRAS_PARTIDAS,
    FACT_SALDOS_MENSUALES,
    FACT_VENTAS_ARTICULO,
    INVENTARIO_EXISTENCIAS,
    VENTAS_DOCUMENTOS,
)

TODAY = date(2026, 9, 10)


class TestDateHelpers:
    def test_iter_chunks_inclusive_and_truncated(self):
        chunks = list(iter_chunks(date(2025, 1, 1), date(2025, 1, 10), 7))
        assert chunks == [
            (date(2025, 1, 1), date(2025, 1, 7)),
            (date(2025, 1, 8), date(2025, 1, 10)),
        ]

    def test_iter_chunks_single_day(self):
        assert list(iter_chunks(date(2025, 1, 1), date(2025, 1, 1), 7)) == [
            (date(2025, 1, 1), date(2025, 1, 1))
        ]

    def test_iter_chunks_empty_when_start_after_end(self):
        assert list(iter_chunks(date(2025, 1, 2), date(2025, 1, 1), 7)) == []

    def test_iter_chunks_rejects_zero_days(self):
        with pytest.raises(ValueError):
            list(iter_chunks(date(2025, 1, 1), date(2025, 1, 2), 0))

    def test_iter_months(self):
        assert list(iter_months(date(2025, 11, 15), date(2026, 1, 3))) == [
            date(2025, 11, 1),
            date(2025, 12, 1),
            date(2026, 1, 1),
        ]

    def test_add_months_across_year(self):
        assert add_months(date(2025, 12, 31), 1) == date(2026, 1, 1)
        assert add_months(date(2026, 1, 15), -1) == date(2025, 12, 1)

    def test_month_last_day(self):
        assert month_last_day(date(2024, 2, 1)) == 29
        assert month_last_day(date(2025, 9, 1)) == 30


@pytest.fixture
def parts():
    api = MagicMock()
    loader = MagicMock()
    state = MagicMock()
    state.get_last_sync_date.return_value = None
    pipeline = Pipeline(
        api,
        loader,
        state,
        empresa="ALMACENES PACHECO",
        backfill_start=date(2023, 9, 10),
        rolling_window_days=45,
        ventas_chunk_days=7,
        compras_chunk_days=31,
        today=TODAY,
    )
    return api, loader, state, pipeline


class TestFactRange:
    def test_chunks_fetch_and_replace_range(self, parts):
        api, loader, state, p = parts
        api.fetch_chunk.side_effect = [
            [{"DOCTO_VE_ID": 1, "FECHA": "2025-01-01", "ALMACEN_ID": 1, "ARTICULO_ID": 9,
              "UNIDADES": "2.000", "FECHA_HORA_ULT_MODIF": "2025-01-01T08:00:00", "EXTRA": 1}],
            [],
        ]
        p._sync_fact_range(FACT_VENTAS_ARTICULO, date(2025, 1, 1), date(2025, 1, 10), state_key="k")

        assert api.fetch_chunk.call_args_list == [
            call("/etl/ventas-articulo", {"fecha_inicio": "2025-01-01", "fecha_fin": "2025-01-07"}),
            call("/etl/ventas-articulo", {"fecha_inicio": "2025-01-08", "fecha_fin": "2025-01-10"}),
        ]
        assert loader.load_replace_range.call_count == 2
        first = loader.load_replace_range.call_args_list[0]
        table, rows, schema, field, a, b = first.args
        assert (table, field, a, b) == ("fact_ventas_articulo", "FECHA", date(2025, 1, 1), date(2025, 1, 7))
        assert schema is FACT_VENTAS_ARTICULO.schema
        row = rows[0]
        assert row["EMPRESA"] == "ALMACENES PACHECO"
        assert row["_synced_at"]
        assert "EXTRA" not in row
        assert row["FECHA_HORA_ULT_MODIF"] == "2025-01-01 08:00:00"
        assert row["UNIDADES"] == "2.000"
        # empty chunk still replaces (clears) the range
        second = loader.load_replace_range.call_args_list[1]
        assert second.args[1] == []
        assert state.record_sync.call_args_list == [
            call("k", date(2025, 1, 7), 1, "success"),
            call("k", date(2025, 1, 10), 0, "success"),
        ]

    def test_keyset_fact_uses_fetch_keyset(self, parts):
        api, loader, state, p = parts
        api.fetch_keyset.return_value = []
        p._sync_fact_range(FACT_COMPRAS_PARTIDAS, date(2025, 1, 1), date(2025, 1, 31))
        api.fetch_keyset.assert_called_once_with(
            "/etl/compras-partidas", {"fecha_inicio": "2025-01-01", "fecha_fin": "2025-01-31"}
        )
        api.fetch_chunk.assert_not_called()


class TestBackfillResume:
    def test_resume_from_state(self, parts):
        api, loader, state, p = parts
        state.get_last_sync_date.return_value = date(2024, 5, 31)
        assert p._resume_from("fact_ventas_articulo", None) == date(2024, 6, 1)
        state.get_last_sync_date.assert_called_with("fact_ventas_articulo:backfill")

    def test_resume_defaults_to_backfill_start(self, parts):
        _, _, _, p = parts
        assert p._resume_from("fact_ventas_articulo", None) == date(2023, 9, 10)

    def test_explicit_start_wins(self, parts):
        api, loader, state, p = parts
        state.get_last_sync_date.return_value = date(2024, 5, 31)
        assert p._resume_from("x", date(2025, 1, 1)) == date(2025, 1, 1)

    def test_backfill_saldos_loads_opening_then_months(self, parts):
        api, loader, state, p = parts
        api.fetch_chunk.return_value = [{"ARTICULO_ID": 1, "ALMACEN_ID": 2, "EXISTENCIA": "5", "COSTO": "50"}]
        p._backfill_saldos(date(2026, 7, 15), date(2026, 8, 20))

        # opening balance for June, then July and August
        assert api.fetch_chunk.call_args_list[0] == call("/etl/saldos-iniciales", {"hasta": "2026-06"})
        assert api.fetch_chunk.call_args_list[1] == call("/etl/saldos-mensuales", {"anio": 2026, "mes": 7})
        assert api.fetch_chunk.call_args_list[2] == call("/etl/saldos-mensuales", {"anio": 2026, "mes": 8})
        opening = loader.load_replace_months.call_args_list[0]
        rows = opening.args[1]
        assert opening.args[4] == [date(2026, 6, 1)]
        assert rows[0]["ES_SALDO_INICIAL"] is True
        assert rows[0]["ENTRADAS_UNIDADES"] == "5"
        assert rows[0]["SALIDAS_UNIDADES"] == 0
        assert rows[0]["PERIODO"] == "2026-06-01"
        assert rows[0]["ULTIMO_DIA"] == 30


class TestNightly:
    def test_collects_failures_and_raises(self, parts):
        api, loader, state, p = parts
        api.fetch_all.side_effect = httpx.ConnectError("down")  # catalogs + headers fail
        api.fetch_keyset.return_value = []
        api.fetch_chunk.return_value = []

        with pytest.raises(PipelineError) as exc:
            p.run_nightly()

        msg = str(exc.value)
        assert "dim_clientes" in msg and "ventas_documentos" in msg
        assert "fact_ventas_articulo" not in msg
        # facts still ran despite catalog failure
        assert loader.load_replace_range.called
        loader.load_partition.assert_called_once()
        error_calls = [c for c in state.record_sync.call_args_list if c.args[3] == "error"]
        assert any(c.args[0] == "dim_clientes" for c in error_calls)

    def test_unexpected_error_propagates(self, parts):
        api, loader, state, p = parts
        api.fetch_all.side_effect = KeyError("boom")
        with pytest.raises(KeyError):
            p.run_nightly()

    def test_snapshot_uses_today_partition(self, parts):
        api, loader, state, p = parts
        api.fetch_chunk.return_value = [{"ARTICULO_ID": 1, "ALMACEN_ID": 2, "EXISTENCIA": "3", "COSTO": "9"}]
        p._sync_snapshot()
        api.fetch_chunk.assert_called_once_with("/etl/saldos-iniciales", {"hasta": "2026-09"})
        table, rows, schema, day = loader.load_partition.call_args.args
        assert table == "inventario_existencias" and day == TODAY
        assert rows[0]["_snapshot_date"] == "2026-09-10"
        assert schema is INVENTARIO_EXISTENCIAS.schema

    def test_saldos_nightly_replaces_current_and_previous_month(self, parts):
        api, loader, state, p = parts
        api.fetch_chunk.return_value = []
        p.sync_saldos()
        assert loader.load_replace_months.call_args.args[4] == [date(2026, 8, 1), date(2026, 9, 1)]

    def test_headers_merge_all_endpoints(self, parts):
        api, loader, state, p = parts
        api.fetch_all.side_effect = lambda *a, **k: [{"DOCTO_VE_ID": 1}]
        p.sync_transactions()
        assert api.fetch_all.call_count == 5
        start = api.fetch_all.call_args_list[0].args[1]["fecha_inicio"]
        assert start == "2026-07-27"  # today - 45
        rows = loader.load_incremental.call_args.args[1]
        assert len(rows) == 5
        assert {r["TIPO_DOCTO"] for r in rows} == {"F", "R", "P", "C", "D"}
        assert rows[0]["EMPRESA"] == "ALMACENES PACHECO"

    def test_retention_override(self, parts):
        _, _, _, p = parts
        cfg = p._with_retention(FACT_VENTAS_ARTICULO)
        assert cfg.expiration_days == 1100
        assert p._with_retention(FACT_SALDOS_MENSUALES).expiration_days is None
        assert p._with_retention(VENTAS_DOCUMENTOS).expiration_days == 1100
