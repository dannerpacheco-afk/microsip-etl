"""Tests for bq_loader.py — validation and temp file handling."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from bq_loader import _validate_identifier


class TestValidateIdentifier:
    def test_valid_identifiers(self):
        assert _validate_identifier("my_table") == "my_table"
        assert _validate_identifier("dim_clientes") == "dim_clientes"
        assert _validate_identifier("Table123") == "Table123"

    def test_invalid_identifiers(self):
        with pytest.raises(ValueError, match="Invalid BigQuery"):
            _validate_identifier("my table")  # space

        with pytest.raises(ValueError, match="Invalid BigQuery"):
            _validate_identifier("table; DROP TABLE x")  # SQL injection

        with pytest.raises(ValueError, match="Invalid BigQuery"):
            _validate_identifier("table`name")  # backtick

        with pytest.raises(ValueError, match="Invalid BigQuery"):
            _validate_identifier("")  # empty


class TestRowsToNdjson:
    """Test the NDJSON file writer indirectly via BigQueryLoader."""

    def test_ndjson_output(self, tmp_path):
        """Test that NDJSON files are written correctly."""
        # We test the standalone logic without needing a BQ client
        from bq_loader import BigQueryLoader
        from unittest.mock import patch, MagicMock

        with patch("bq_loader.bigquery.Client"):
            loader = BigQueryLoader("proj", "ds", "us-central1")

        rows = [
            {"id": 1, "name": "test"},
            {"id": 2, "name": "otro"},
        ]

        ndjson_path = loader._rows_to_ndjson_file(rows)
        try:
            with open(ndjson_path) as f:
                lines = f.readlines()
            assert len(lines) == 2
            assert json.loads(lines[0]) == {"id": 1, "name": "test"}
            assert json.loads(lines[1]) == {"id": 2, "name": "otro"}
        finally:
            Path(ndjson_path).unlink(missing_ok=True)
