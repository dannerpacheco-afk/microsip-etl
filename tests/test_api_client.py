"""Tests for api_client.py — MicrosipClient."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

# Add project root to path for imports
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api_client import MicrosipClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_httpx_client():
    """Patch httpx.Client so no real HTTP calls are made."""
    with patch("api_client.httpx.Client") as mock_cls:
        mock_instance = MagicMock()
        mock_cls.return_value = mock_instance
        yield mock_instance


@pytest.fixture
def client(mock_httpx_client):
    return MicrosipClient(
        base_url="http://localhost:8000/api/v1",
        api_key="test-key",
        page_size=2,
        max_pages=5,
    )


# ---------------------------------------------------------------------------
# _request_with_retry
# ---------------------------------------------------------------------------

class TestRequestWithRetry:
    def test_success_on_first_try(self, client, mock_httpx_client):
        resp = MagicMock(status_code=200)
        resp.raise_for_status = MagicMock()
        mock_httpx_client.get.return_value = resp

        result = client._request_with_retry("http://example.com", {})
        assert result is resp
        assert mock_httpx_client.get.call_count == 1

    def test_retry_on_429_then_success(self, client, mock_httpx_client):
        resp_429 = MagicMock(status_code=429, headers={})
        resp_200 = MagicMock(status_code=200)
        resp_200.raise_for_status = MagicMock()
        mock_httpx_client.get.side_effect = [resp_429, resp_200]

        with patch("api_client.time.sleep"):
            result = client._request_with_retry("http://example.com", {})

        assert result is resp_200
        assert mock_httpx_client.get.call_count == 2

    def test_exhausted_retries_raises(self, client, mock_httpx_client):
        resp_429 = MagicMock(status_code=429, headers={})
        resp_429.request = MagicMock()
        mock_httpx_client.get.return_value = resp_429

        with patch("api_client.time.sleep"):
            with pytest.raises(httpx.HTTPStatusError, match="Rate limited"):
                client._request_with_retry(
                    "http://example.com", {}, max_retries=2
                )

    def test_max_retries_zero_raises_valueerror(self, client, mock_httpx_client):
        with pytest.raises(ValueError, match="max_retries must be >= 1"):
            client._request_with_retry("http://example.com", {}, max_retries=0)

    def test_non_2xx_raises_immediately(self, client, mock_httpx_client):
        resp_500 = MagicMock(status_code=500)
        resp_500.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Server Error", request=MagicMock(), response=resp_500
        )
        mock_httpx_client.get.return_value = resp_500

        with pytest.raises(httpx.HTTPStatusError):
            client._request_with_retry("http://example.com", {})

        # Should NOT retry on 500
        assert mock_httpx_client.get.call_count == 1


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

class TestPagination:
    def test_single_page(self, client, mock_httpx_client):
        resp = MagicMock(status_code=200)
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "data": [{"id": 1}, {"id": 2}],
            "has_more": False,
        }
        mock_httpx_client.get.return_value = resp

        rows = client.fetch_all("/test")
        assert rows == [{"id": 1}, {"id": 2}]

    def test_multi_page(self, client, mock_httpx_client):
        resp1 = MagicMock(status_code=200)
        resp1.raise_for_status = MagicMock()
        resp1.json.return_value = {"data": [{"id": 1}, {"id": 2}], "has_more": True}

        resp2 = MagicMock(status_code=200)
        resp2.raise_for_status = MagicMock()
        resp2.json.return_value = {"data": [{"id": 3}], "has_more": False}

        mock_httpx_client.get.side_effect = [resp1, resp2]

        with patch("api_client.time.sleep"):
            rows = client.fetch_all("/test")

        assert rows == [{"id": 1}, {"id": 2}, {"id": 3}]

    def test_empty_first_page(self, client, mock_httpx_client):
        resp = MagicMock(status_code=200)
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"data": [], "has_more": False}
        mock_httpx_client.get.return_value = resp

        rows = client.fetch_all("/test")
        assert rows == []

    def test_max_pages_safety_valve(self, client, mock_httpx_client):
        """Ensure pagination stops at max_pages even if has_more is True."""
        resp = MagicMock(status_code=200)
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"data": [{"id": 1}], "has_more": True}
        mock_httpx_client.get.return_value = resp

        # client.max_pages = 5
        with patch("api_client.time.sleep"):
            rows = client.fetch_all("/test")

        assert len(rows) == 5  # 5 pages × 1 record each
        assert mock_httpx_client.get.call_count == 5


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------

class TestContextManager:
    def test_close_on_exit(self, mock_httpx_client):
        with MicrosipClient("http://localhost", "key") as c:
            pass
        mock_httpx_client.close.assert_called_once()
