from __future__ import annotations

import logging
import time
from collections.abc import Iterator

import httpx

logger = logging.getLogger(__name__)

# Default max pages to prevent infinite pagination loops
DEFAULT_MAX_PAGES = 10_000

# Transient failures worth retrying (the bulk endpoints run long Firebird
# queries; a dropped connection or a proxy timeout should not kill a backfill).
_RETRYABLE_STATUS = {429, 502, 503, 504}


class MicrosipClient:
    """HTTP client for the Microsip REST API.

    Supports three access patterns:
    - ``fetch_all``: legacy offset pagination (``skip``/``limit``).
    - ``fetch_keyset``: cursor pagination used by ``/etl/*`` endpoints
      (``cursor``/``next_cursor``).
    - ``fetch_chunk``: single request that returns a whole chunk
      (``/etl/ventas-articulo``, ``/etl/saldos-*``).
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        page_size: int = 500,
        max_pages: int = DEFAULT_MAX_PAGES,
        empresa: str | None = None,
        timeout: float = 60.0,
        bulk_page_size: int = 5000,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.page_size = page_size
        self.bulk_page_size = bulk_page_size
        self.max_pages = max_pages
        headers = {"X-API-Key": api_key}
        if empresa:
            headers["X-Empresa"] = empresa
        self._client = httpx.Client(headers=headers, timeout=timeout)

    # --- Public API ---

    def fetch_all(self, endpoint: str, params: dict | None = None) -> list[dict]:
        """Fetch all pages from an offset-paginated endpoint."""
        all_rows: list[dict] = []
        for page in self._paginate(endpoint, params):
            all_rows.extend(page)
        return all_rows

    def fetch_keyset(
        self, endpoint: str, params: dict | None = None
    ) -> list[dict]:
        """Fetch all pages from a keyset-paginated ``/etl`` endpoint."""
        all_rows: list[dict] = []
        for page in self._paginate_keyset(endpoint, params):
            all_rows.extend(page)
        return all_rows

    def fetch_chunk(self, endpoint: str, params: dict | None = None) -> list[dict]:
        """Single request; the endpoint returns the whole chunk in ``data``."""
        url = f"{self.base_url}{endpoint}"
        response = self._request_with_retry(url, dict(params or {}))
        body = response.json()
        data = body.get("data", [])
        if body.get("has_more"):
            # Chunk endpoints are not paginated; has_more=True means the
            # server truncated the result and the chunk must be smaller.
            raise RuntimeError(
                f"{endpoint} returned has_more=True for params {params}; "
                "reduce the chunk size."
            )
        return data

    # --- Pagination generators ---

    def _paginate(self, endpoint: str, params: dict | None = None) -> Iterator[list[dict]]:
        """Generator yielding pages of records from an offset-paginated endpoint."""
        skip = 0
        page_count = 0
        base_params = dict(params or {})
        base_params["limit"] = self.page_size
        base_params["include_total"] = "false"

        while True:
            if page_count >= self.max_pages:
                logger.error(
                    "Pagination safety limit reached (%d pages) for %s. "
                    "Stopping to prevent infinite loop.",
                    self.max_pages,
                    endpoint,
                )
                break

            url = f"{self.base_url}{endpoint}"

            response = self._request_with_retry(url, {**base_params, "skip": skip})
            body = response.json()

            data = body.get("data", [])
            if not data:
                break

            yield data
            page_count += 1
            logger.debug(
                "Fetched %d records from %s (skip=%d, page=%d)",
                len(data),
                endpoint,
                skip,
                page_count,
            )

            has_more = body.get("has_more", False)
            if not has_more:
                break

            skip += self.page_size
            time.sleep(0.1)  # respect rate limit (120 req/min)

    def _paginate_keyset(
        self, endpoint: str, params: dict | None = None
    ) -> Iterator[list[dict]]:
        """Generator yielding pages from a keyset-paginated ``/etl`` endpoint."""
        cursor = 0
        page_count = 0
        base_params = dict(params or {})
        base_params["limit"] = self.bulk_page_size

        while True:
            if page_count >= self.max_pages:
                logger.error(
                    "Pagination safety limit reached (%d pages) for %s.",
                    self.max_pages,
                    endpoint,
                )
                break

            url = f"{self.base_url}{endpoint}"
            response = self._request_with_retry(url, {**base_params, "cursor": cursor})
            body = response.json()

            data = body.get("data", [])
            if not data:
                break

            yield data
            page_count += 1
            logger.debug(
                "Fetched %d records from %s (cursor=%d, page=%d)",
                len(data),
                endpoint,
                cursor,
                page_count,
            )

            if not body.get("has_more", False):
                break

            next_cursor = body.get("next_cursor")
            if next_cursor is None or next_cursor <= cursor:
                logger.error(
                    "Keyset pagination stalled on %s (cursor=%s, next=%s)",
                    endpoint,
                    cursor,
                    next_cursor,
                )
                break
            cursor = next_cursor
            time.sleep(0.1)

    # --- HTTP ---

    def _request_with_retry(
        self, url: str, params: dict, max_retries: int = 4
    ) -> httpx.Response:
        """GET with retry on 429/5xx and transport errors.

        Raises:
            httpx.HTTPStatusError: non-2xx after retries are exhausted.
            httpx.TransportError: connection/timeout errors after retries.
        """
        if max_retries < 1:
            raise ValueError("max_retries must be >= 1")

        last_response: httpx.Response | None = None
        last_error: httpx.TransportError | None = None
        for attempt in range(max_retries):
            try:
                response = self._client.get(url, params=params)
            except httpx.TransportError as exc:
                last_error = exc
                wait = min(2**attempt * 5, 60)
                logger.warning(
                    "Transport error on %s (%s), retry %d/%d in %ds",
                    url,
                    type(exc).__name__,
                    attempt + 1,
                    max_retries,
                    wait,
                )
                time.sleep(wait)
                continue

            last_response = response
            if response.status_code in _RETRYABLE_STATUS:
                wait = min(2**attempt * 5, 60)
                retry_after = response.headers.get("Retry-After")
                if retry_after:
                    try:
                        wait = int(retry_after)
                    except ValueError:
                        pass
                logger.warning(
                    "HTTP %d on %s, retry %d/%d in %ds",
                    response.status_code,
                    url,
                    attempt + 1,
                    max_retries,
                    wait,
                )
                time.sleep(wait)
                continue

            response.raise_for_status()
            return response

        if last_response is not None:
            reason = (
                "Rate limited"
                if last_response.status_code == 429
                else f"HTTP {last_response.status_code}"
            )
            raise httpx.HTTPStatusError(
                f"{reason} after {max_retries} retries",
                request=last_response.request,
                response=last_response,
            )
        assert last_error is not None
        raise last_error

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
