"""Tests for config.py — Settings validation."""

from __future__ import annotations

import os
import pytest

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Set dummy env vars BEFORE importing config, since the module-level
# `settings = Settings()` fires at import time.
os.environ.setdefault("MICROSIP_API_KEY", "test-dummy-key")
os.environ.setdefault("GCP_PROJECT_ID", "test-dummy-project")

from config import Settings


class TestSettingsValidation:
    def test_empty_api_key_raises(self):
        with pytest.raises(Exception, match="MICROSIP_API_KEY is required"):
            Settings(
                microsip_api_key="",
                gcp_project_id="test-project",
                _env_file=None,
            )

    def test_placeholder_api_key_raises(self):
        with pytest.raises(Exception, match="MICROSIP_API_KEY is required"):
            Settings(
                microsip_api_key="your-api-key-here",
                gcp_project_id="test-project",
                _env_file=None,
            )

    def test_empty_project_id_raises(self):
        with pytest.raises(Exception, match="GCP_PROJECT_ID is required"):
            Settings(
                microsip_api_key="real-key",
                gcp_project_id="",
                _env_file=None,
            )

    def test_valid_settings(self):
        s = Settings(
            microsip_api_key="real-key",
            gcp_project_id="test-project",
            _env_file=None,
        )
        assert s.microsip_api_key == "real-key"
        assert s.gcp_project_id == "test-project"

    def test_http_warning_for_non_localhost(self):
        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            Settings(
                microsip_api_url="http://192.168.1.100:8000/api/v1",
                microsip_api_key="real-key",
                gcp_project_id="test-project",
                _env_file=None,
            )
            http_warnings = [x for x in w if "cleartext" in str(x.message)]
            assert len(http_warnings) == 1

    def test_no_warning_for_localhost(self):
        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            Settings(
                microsip_api_url="http://localhost:8000/api/v1",
                microsip_api_key="real-key",
                gcp_project_id="test-project",
                _env_file=None,
            )
            http_warnings = [x for x in w if "cleartext" in str(x.message)]
            assert len(http_warnings) == 0

    def test_max_pages_default(self):
        s = Settings(
            microsip_api_key="real-key",
            gcp_project_id="test-project",
            _env_file=None,
        )
        assert s.max_pages == 10_000
