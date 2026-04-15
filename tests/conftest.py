"""Shared test fixtures and configuration."""

from __future__ import annotations

import os
import sys

# Add project root to sys.path so tests can import project modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Set dummy env vars BEFORE any module imports config.py,
# since `settings = Settings()` fires at module level.
os.environ.setdefault("MICROSIP_API_KEY", "test-dummy-key")
os.environ.setdefault("GCP_PROJECT_ID", "test-dummy-project")
