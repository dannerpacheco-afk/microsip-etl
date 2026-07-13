"""Locate Microsip articulos that cause 502 errors.

Diagnostic tool. The API now CASTs ARTICULOS.NOMBRE to OCTETS to
work around the ISO8859_1 → WIN1252 transliteration that used to
break pagination, so a clean run should report "No bad articles
found". If similar charset issues resurface (e.g. a different
column gets bad bytes), this script binary-searches the catalog,
narrows the failure to specific skip offsets, then probes
individual ARTICULO_IDs via GET /articulos/{id} to identify the
exact records to fix in Microsip.

Usage:
    docker compose run --rm etl find-bad-articulos
        Scans the full ARTICULOS catalog (default 0 → 15000).

    docker compose run --rm etl find-bad-articulos <start> <end>
        Scans a custom skip range, e.g.:
            docker compose run --rm etl find-bad-articulos 0 5000
"""

from __future__ import annotations

import logging
import sys
import time

import httpx

from config import settings

logger = logging.getLogger(__name__)

# Default upper bound for the full-catalog scan. The catalog has ~12k
# articulos today; 15k leaves room for growth without missing rows.
# Override by passing start/end as CLI args.
DEFAULT_SCAN_END = 15_000
PAGE_WIDTH = 100

# API rate limit is 120 req/min. Stay at ~100 req/min (~0.6s per request)
# to leave headroom for bursts and avoid hitting 429s.
RATE_LIMIT_DELAY_SEC = 0.6


def _request(
    client: httpx.Client,
    url: str,
    params: dict | None = None,
    max_retries: int = 5,
) -> httpx.Response:
    """GET with rate-limit retry + a small delay between calls.

    Returns the response even if it's non-2xx (caller checks status).
    Retries automatically on 429, honoring Retry-After when present.
    """
    for attempt in range(max_retries):
        response = client.get(url, params=params)
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            try:
                wait = int(retry_after) if retry_after else 2**attempt
            except ValueError:
                wait = 2**attempt
            wait = min(wait, 30)
            logger.info(
                "Rate limited (429), waiting %ds (attempt %d/%d)...",
                wait,
                attempt + 1,
                max_retries,
            )
            time.sleep(wait)
            continue
        time.sleep(RATE_LIMIT_DELAY_SEC)
        return response
    # Exhausted retries — return whatever the last response was
    time.sleep(RATE_LIMIT_DELAY_SEC)
    return response


def _get_status(client: httpx.Client, skip: int, limit: int) -> int:
    """Return HTTP status code for a single /articulos page request."""
    response = _request(
        client,
        f"{settings.microsip_api_url}/articulos",
        params={"limit": limit, "skip": skip, "include_total": "false"},
    )
    return response.status_code


def _get_articulo(client: httpx.Client, skip: int) -> dict | None:
    """Fetch a single articulo at the given skip, or None if non-200."""
    response = _request(
        client,
        f"{settings.microsip_api_url}/articulos",
        params={"limit": 1, "skip": skip, "include_total": "false"},
    )
    if response.status_code != 200:
        return None
    data = response.json().get("data", [])
    return data[0] if data else None


def _binary_search(
    client: httpx.Client, start: int, end: int
) -> list[int]:
    """Recursively find all skip offsets in [start, end) that return 502.

    Assumes at least one bad offset exists in the range.
    """
    if start >= end:
        return []

    size = end - start
    if size == 1:
        # Leaf: verify this exact position fails
        return [start] if _get_status(client, start, limit=1) == 502 else []

    mid = start + size // 2
    left_size = mid - start
    right_size = end - mid

    left_status = _get_status(client, start, limit=left_size)
    right_status = _get_status(client, mid, limit=right_size)

    bad: list[int] = []
    if left_status != 200:
        bad.extend(_binary_search(client, start, mid))
    if right_status != 200:
        bad.extend(_binary_search(client, mid, end))
    return bad


def _scan_pages(
    client: httpx.Client, pages: list[tuple[int, int]]
) -> list[int]:
    """Binary-search each (start, end) range for bad offsets."""
    all_bad: list[int] = []
    for start, end in pages:
        logger.info(
            "Searching range [%d, %d) for bad offsets...", start, end
        )
        bad = _binary_search(client, start, end)
        if bad:
            logger.info("  → Found %d bad offset(s) in range: %s", len(bad), bad)
        else:
            logger.info("  → Range is clean (no bad offsets)")
        all_bad.extend(bad)
    return all_bad


def _group_consecutive(offsets: list[int]) -> list[list[int]]:
    """Group a sorted list of ints into sublists of consecutive runs."""
    if not offsets:
        return []
    groups: list[list[int]] = []
    current: list[int] = [offsets[0]]
    for x in offsets[1:]:
        if x == current[-1] + 1:
            current.append(x)
        else:
            groups.append(current)
            current = [x]
    groups.append(current)
    return groups


def _probe_id(client: httpx.Client, articulo_id: int) -> int:
    """Return HTTP status for GET /articulos/{id}."""
    response = _request(
        client,
        f"{settings.microsip_api_url}/articulos/{articulo_id}",
    )
    return response.status_code


def _find_exact_bad_ids(
    client: httpx.Client, low_id: int, high_id: int
) -> list[int]:
    """Test each ARTICULO_ID in (low_id, high_id) via GET /articulos/{id}.

    Returns IDs that return 500/502 (transliteration failure).
    """
    bad_ids: list[int] = []
    total = high_id - low_id - 1
    for i, articulo_id in enumerate(range(low_id + 1, high_id), start=1):
        status = _probe_id(client, articulo_id)
        if status in (500, 502):
            bad_ids.append(articulo_id)
            logger.info(
                "    [%d/%d] ARTICULO_ID=%d → %d BAD",
                i,
                total,
                articulo_id,
                status,
            )
        # 404 = ID doesn't exist (gap in the sequence), skip silently
        # 200 = OK, skip silently
    return bad_ids


def _report(client: httpx.Client, bad_offsets: list[int]) -> None:
    """Group bad offsets into consecutive runs and find exact IDs."""
    if not bad_offsets:
        logger.info("")
        logger.info("No bad articles found.")
        return

    groups = _group_consecutive(sorted(bad_offsets))

    logger.info("")
    logger.info("=" * 70)
    logger.info(
        "Found %d bad article(s) in %d group(s). Scanning exact IDs...",
        len(bad_offsets),
        len(groups),
    )
    logger.info("=" * 70)

    # Silence httpx while we probe individual IDs (too many calls)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    all_exact: list[tuple[list[int], int, int]] = []
    for group in groups:
        first = group[0]
        last = group[-1]
        before = _get_articulo(client, first - 1) if first > 0 else None
        after = _get_articulo(client, last + 1)
        prev_id = before.get("ARTICULO_ID") if before else None
        next_id = after.get("ARTICULO_ID") if after else None

        logger.info("")
        logger.info(
            "Group: skip=%s → %d bad row(s) between ID %s and ID %s",
            group,
            len(group),
            prev_id,
            next_id,
        )
        if before:
            logger.info(
                "  ← Prev OK (ID=%s): %s",
                prev_id,
                str(before.get("NOMBRE", ""))[:60],
            )
        if after:
            logger.info(
                "  → Next OK (ID=%s): %s",
                next_id,
                str(after.get("NOMBRE", ""))[:60],
            )

        if prev_id is None or next_id is None:
            logger.warning("  (Cannot scan — missing neighbor ID)")
            continue

        logger.info(
            "  Probing %d candidate IDs...", next_id - prev_id - 1
        )
        exact = _find_exact_bad_ids(client, prev_id, next_id)
        logger.info("  → Exact bad ARTICULO_IDs: %s", exact)
        all_exact.append((exact, prev_id, next_id))

    # Re-enable httpx logging
    logging.getLogger("httpx").setLevel(logging.INFO)

    # Final consolidated list
    flat = [aid for group_result in all_exact for aid in group_result[0]]
    logger.info("")
    logger.info("=" * 70)
    logger.info("FINAL LIST — %d bad ARTICULO_ID(s) to fix in Microsip:", len(flat))
    logger.info("=" * 70)
    for aid in flat:
        logger.info("  %d", aid)

    logger.info("")
    logger.info("Next steps:")
    logger.info("  1. Open Microsip → Inventarios → Catalogos → Articulos")
    logger.info("  2. Find each ARTICULO_ID above and inspect text fields")
    logger.info("     (NOMBRE, OBSERVACIONES, CLAVES, NOTAS) for weird")
    logger.info("     characters: smart quotes, em-dashes, emojis, ™, ©, etc.")
    logger.info("  3. Replace them with plain equivalents and save")
    logger.info("  4. Re-run 'docker compose run --rm etl find-bad-articulos'")
    logger.info("     to confirm all are fixed (should report 0 bad).")
    logger.info("  5. Then re-run 'docker compose run --rm etl catalogs' to")
    logger.info("     load the full articulos catalog into BigQuery.")


def run() -> None:
    """CLI entry point."""
    # Parse optional range from argv (after the target name)
    args = sys.argv[2:] if len(sys.argv) > 2 else []

    if len(args) == 2:
        start, end = int(args[0]), int(args[1])
    else:
        start, end = 0, DEFAULT_SCAN_END

    pages = [
        (s, min(s + PAGE_WIDTH, end))
        for s in range(start, end, PAGE_WIDTH)
    ]
    logger.info(
        "Scanning range [%d, %d) in %d chunks of %d...",
        start,
        end,
        len(pages),
        PAGE_WIDTH,
    )

    with httpx.Client(
        headers={"X-API-Key": settings.microsip_api_key},
        timeout=60.0,
    ) as client:
        # First pass: identify which chunks fail. With the API charset
        # fix in place this should typically be empty.
        bad_pages: list[tuple[int, int]] = []
        for p_start, p_end in pages:
            status = _get_status(client, p_start, limit=p_end - p_start)
            if status != 200:
                bad_pages.append((p_start, p_end))

        if not bad_pages:
            logger.info("All %d chunks returned 200. No bad articles found.", len(pages))
            return

        logger.info("Found %d bad chunk(s): %s", len(bad_pages), bad_pages)
        bad_offsets = _scan_pages(client, bad_pages)
        _report(client, bad_offsets)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    run()
