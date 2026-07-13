from __future__ import annotations

import logging
import time

import httpx

logger = logging.getLogger(__name__)

# Default max pages to prevent infinite pagination loops
DEFAULT_MAX_PAGES = 10_000


class MicrosipClient:
    """HTTP client for the Microsip REST API with pagination support."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        page_size: int = 500,
        max_pages: int = DEFAULT_MAX_PAGES,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.page_size = page_size
        self.max_pages = max_pages
        self._client = httpx.Client(
            headers={"X-API-Key": api_key},
            timeout=60.0,
        )

    def fetch_all(
        self,
        endpoint: str,
        params: dict | None = None,
        page_size: int | None = None,
        skip_failed_pages: bool = False,
    ) -> list[dict]:
        """Fetch all pages from a paginated endpoint. Returns flat list of records.

        Args:
            endpoint: API path (e.g. "/articulos").
            params: Query parameters.
            page_size: Override the client's default page size for this call.
            skip_failed_pages: If True, log and skip pages that fail after
                all retries instead of raising. Useful for endpoints with
                known bad rows (e.g. charset issues in Microsip data).
        """
        effective_size = page_size or self.page_size
        all_rows: list[dict] = []
        for page in self._paginate(
            endpoint, params, effective_size, skip_failed_pages
        ):
            all_rows.extend(page)
        return all_rows

    def _paginate(
        self,
        endpoint: str,
        params: dict | None,
        page_size: int,
        skip_failed_pages: bool,
    ):
        """Generator yielding pages of records from a paginated endpoint."""
        skip = 0
        page_count = 0
        failed_pages: list[int] = []
        base_params = dict(params or {})
        base_params["limit"] = page_size
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

            base_params["skip"] = skip
            url = f"{self.base_url}{endpoint}"

            try:
                response = self._request_with_retry(url, base_params)
            except httpx.HTTPStatusError:
                if skip_failed_pages:
                    logger.error(
                        "Page failed after retries: %s skip=%d — skipping "
                        "(skip_failed_pages=True). Data in this page will "
                        "NOT be loaded to BigQuery.",
                        endpoint,
                        skip,
                    )
                    failed_pages.append(skip)
                    # Advance past the failed page and keep going. We can't
                    # know if there are more rows beyond; rely on an empty
                    # response or max_pages to terminate.
                    skip += page_size
                    page_count += 1
                    time.sleep(0.1)
                    continue
                raise

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

            skip += page_size
            time.sleep(0.1)  # respect rate limit (120 req/min)

        if failed_pages:
            logger.warning(
                "%s: %d page(s) failed and were skipped (skip offsets: %s)",
                endpoint,
                len(failed_pages),
                failed_pages,
            )

    # HTTP status codes that should trigger a retry with backoff.
    # 429 = rate limited; 5xx = transient server errors (upstream DB
    # timeouts, worker crashes, etc.) common with heavy Firebird queries.
    _RETRYABLE_STATUS = {429, 500, 502, 503, 504}

    def _request_with_retry(
        self, url: str, params: dict, max_retries: int = 5
    ) -> httpx.Response:
        """Make GET request with retry on rate-limit and transient 5xx errors.

        Raises:
            httpx.HTTPStatusError: If all retries are exhausted or the
                server returns a non-retryable non-2xx status code.
        """
        if max_retries < 1:
            raise ValueError("max_retries must be >= 1")

        last_response: httpx.Response | None = None
        for attempt in range(max_retries):
            response = self._client.get(url, params=params)
            last_response = response

            if response.status_code in self._RETRYABLE_STATUS:
                wait = min(2**attempt, 30)  # cap backoff at 30s
                retry_after = response.headers.get("Retry-After")
                if retry_after:
                    try:
                        wait = int(retry_after)
                    except ValueError:
                        pass
                logger.warning(
                    "Transient error %d from %s, waiting %ds before retry "
                    "(attempt %d/%d)...",
                    response.status_code,
                    url,
                    wait,
                    attempt + 1,
                    max_retries,
                )
                time.sleep(wait)
                continue

            response.raise_for_status()
            return response

        # All retries exhausted
        assert last_response is not None  # always set when max_retries >= 1
        raise httpx.HTTPStatusError(
            f"Request failed after {max_retries} retries "
            f"(last status: {last_response.status_code})",
            request=last_response.request,
            response=last_response,
        )

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
