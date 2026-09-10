"""Tests for api_client.py — keyset pagination, chunk fetch, retries."""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api_client import MicrosipClient


def _resp(data, has_more=False, next_cursor=None, status=200):
    resp = MagicMock(status_code=status, headers={})
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"data": data, "has_more": has_more, "next_cursor": next_cursor}
    return resp


@pytest.fixture
def mock_httpx_client():
    with patch("api_client.httpx.Client") as mock_cls:
        instance = MagicMock()
        mock_cls.return_value = instance
        yield mock_cls, instance


@pytest.fixture
def client(mock_httpx_client):
    return MicrosipClient(
        base_url="http://api/api/v1",
        api_key="k",
        page_size=2,
        max_pages=5,
        empresa="ALMACENES PACHECO",
        timeout=123.0,
        bulk_page_size=3,
    )


class TestConstruction:
    def test_headers_and_timeout(self, mock_httpx_client, client):
        mock_cls, _ = mock_httpx_client
        kwargs = mock_cls.call_args.kwargs
        assert kwargs["headers"] == {"X-API-Key": "k", "X-Empresa": "ALMACENES PACHECO"}
        assert kwargs["timeout"] == 123.0


class TestKeyset:
    def test_follows_next_cursor(self, mock_httpx_client, client):
        _, http = mock_httpx_client
        http.get.side_effect = [
            _resp([{"id": 1}, {"id": 2}, {"id": 3}], has_more=True, next_cursor=3),
            _resp([{"id": 4}], has_more=False, next_cursor=None),
        ]
        with patch("api_client.time.sleep"):
            rows = client.fetch_keyset("/etl/x", {"a": "b"})
        assert [r["id"] for r in rows] == [1, 2, 3, 4]
        first, second = http.get.call_args_list
        assert first.kwargs["params"] == {"a": "b", "limit": 3, "cursor": 0}
        assert second.kwargs["params"] == {"a": "b", "limit": 3, "cursor": 3}
        assert first.args[0] == "http://api/api/v1/etl/x"

    def test_stalled_cursor_stops(self, mock_httpx_client, client):
        _, http = mock_httpx_client
        http.get.return_value = _resp([{"id": 1}], has_more=True, next_cursor=0)
        with patch("api_client.time.sleep"):
            rows = client.fetch_keyset("/etl/x")
        assert rows == [{"id": 1}]
        assert http.get.call_count == 1

    def test_empty(self, mock_httpx_client, client):
        _, http = mock_httpx_client
        http.get.return_value = _resp([])
        assert client.fetch_keyset("/etl/x") == []


class TestChunk:
    def test_returns_data(self, mock_httpx_client, client):
        _, http = mock_httpx_client
        http.get.return_value = _resp([{"id": 1}])
        assert client.fetch_chunk("/etl/y", {"fecha_inicio": "2025-01-01"}) == [{"id": 1}]
        assert http.get.call_args.kwargs["params"] == {"fecha_inicio": "2025-01-01"}

    def test_truncated_raises(self, mock_httpx_client, client):
        _, http = mock_httpx_client
        http.get.return_value = _resp([{"id": 1}], has_more=True)
        with pytest.raises(RuntimeError, match="reduce the chunk size"):
            client.fetch_chunk("/etl/y")


class TestRetries:
    def test_retries_503_then_succeeds(self, mock_httpx_client, client):
        _, http = mock_httpx_client
        http.get.side_effect = [_resp([], status=503), _resp([{"id": 1}])]
        with patch("api_client.time.sleep") as sleep:
            rows = client.fetch_chunk("/etl/y")
        assert rows == [{"id": 1}]
        assert sleep.called

    def test_retries_transport_error_then_succeeds(self, mock_httpx_client, client):
        _, http = mock_httpx_client
        http.get.side_effect = [httpx.ReadTimeout("slow"), _resp([{"id": 1}])]
        with patch("api_client.time.sleep"):
            assert client.fetch_chunk("/etl/y") == [{"id": 1}]

    def test_transport_error_exhausted_raises(self, mock_httpx_client, client):
        _, http = mock_httpx_client
        http.get.side_effect = httpx.ConnectError("down")
        with patch("api_client.time.sleep"):
            with pytest.raises(httpx.ConnectError):
                client._request_with_retry("http://x", {}, max_retries=2)
        assert http.get.call_count == 2

    def test_500_not_retried(self, mock_httpx_client, client):
        _, http = mock_httpx_client
        resp = MagicMock(status_code=500, headers={})
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "boom", request=MagicMock(), response=resp
        )
        http.get.return_value = resp
        with pytest.raises(httpx.HTTPStatusError):
            client._request_with_retry("http://x", {})
        assert http.get.call_count == 1
