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

    def fetch_all(self, endpoint: str, params: dict | None = None) -> list[dict]:
        """Fetch all pages from a paginated endpoint. Returns flat list of records."""
        all_rows: list[dict] = []
        for page in self._paginate(endpoint, params):
            all_rows.extend(page)
        return all_rows

    def _paginate(self, endpoint: str, params: dict | None = None):
        """Generator yielding pages of records from a paginated endpoint."""
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

            base_params["skip"] = skip
            url = f"{self.base_url}{endpoint}"

            response = self._request_with_retry(url, base_params)
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

    def _request_with_retry(
        self, url: str, params: dict, max_retries: int = 3
    ) -> httpx.Response:
        """Make GET request with retry on 429 (rate limited).

        Raises:
            httpx.HTTPStatusError: If all retries are exhausted (429) or
                the server returns a non-2xx status code.
        """
        if max_retries < 1:
            raise ValueError("max_retries must be >= 1")

        last_response: httpx.Response | None = None
        for attempt in range(max_retries):
            response = self._client.get(url, params=params)
            last_response = response

            if response.status_code == 429:
                wait = min(2**attempt, 30)  # cap backoff at 30s
                retry_after = response.headers.get("Retry-After")
                if retry_after:
                    try:
                        wait = int(retry_after)
                    except ValueError:
                        pass
                logger.warning(
                    "Rate limited (429), waiting %ds before retry "
                    "(attempt %d/%d)...",
                    wait,
                    attempt + 1,
                    max_retries,
                )
                time.sleep(wait)
                continue

            response.raise_for_status()
            return response

        # All retries exhausted — raise the last 429 as an error
        assert last_response is not None  # always set when max_retries >= 1
        raise httpx.HTTPStatusError(
            f"Rate limited after {max_retries} retries",
            request=last_response.request,
            response=last_response,
        )

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
