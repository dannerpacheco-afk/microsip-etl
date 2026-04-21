"""Locate Microsip articulos that cause 502 errors.

Uses binary search within each known-bad page to find the exact skip
offset(s) that return 502 Bad Gateway. For each bad offset, reports
the ARTICULO_ID of the previous and next articles so the user can
find the bad one in Microsip and fix its character encoding.

Usage:
    docker compose run --rm etl find-bad-articulos

Or to scan a custom range (start end, in skip units):
    docker compose run --rm etl find-bad-articulos 0 12000
"""

from __future__ import annotations

import logging
import sys

import httpx

from config import settings

logger = logging.getLogger(__name__)

# Known bad pages from the last ETL run (page_size=100 each).
# Override by passing start/end as CLI args.
DEFAULT_BAD_PAGES = [1100, 4800, 6200, 7600, 10200]
PAGE_WIDTH = 100


def _get_status(client: httpx.Client, skip: int, limit: int) -> int:
    """Return HTTP status code for a single /articulos page request."""
    response = client.get(
        f"{settings.microsip_api_url}/articulos",
        params={"limit": limit, "skip": skip, "include_total": "false"},
    )
    return response.status_code


def _get_articulo(client: httpx.Client, skip: int) -> dict | None:
    """Fetch a single articulo at the given skip, or None if 502."""
    response = client.get(
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


def _report(client: httpx.Client, bad_offsets: list[int]) -> None:
    """Fetch neighbor articles for each bad offset and print a summary."""
    if not bad_offsets:
        logger.info("")
        logger.info("No bad articles found. ")
        return

    logger.info("")
    logger.info("=" * 70)
    logger.info("SUMMARY: %d bad article(s) found", len(bad_offsets))
    logger.info("=" * 70)

    for pos in sorted(bad_offsets):
        before = _get_articulo(client, pos - 1) if pos > 0 else None
        after = _get_articulo(client, pos + 1)

        logger.info("")
        logger.info("Bad article at skip=%d:", pos)

        if before:
            logger.info(
                "  ← Previous (skip=%d): ARTICULO_ID=%s  NOMBRE=%r",
                pos - 1,
                before.get("ARTICULO_ID"),
                str(before.get("NOMBRE", ""))[:60],
            )
        if after:
            logger.info(
                "  → Next     (skip=%d): ARTICULO_ID=%s  NOMBRE=%r",
                pos + 1,
                after.get("ARTICULO_ID"),
                str(after.get("NOMBRE", ""))[:60],
            )

        if before and after:
            prev_id = before.get("ARTICULO_ID")
            next_id = after.get("ARTICULO_ID")
            logger.info(
                "  >> The bad article has ARTICULO_ID between %s and %s",
                prev_id,
                next_id,
            )

    logger.info("")
    logger.info("Next steps:")
    logger.info("  1. Open Microsip → Inventarios → Catalogos → Articulos")
    logger.info("  2. Find each bad ARTICULO_ID range listed above")
    logger.info("  3. Look for weird characters in NOMBRE, OBSERVACIONES,")
    logger.info("     or other text fields (smart quotes, em-dashes,")
    logger.info("     emojis, ™, ©, etc.) and replace with plain equivalents")
    logger.info("  4. Save and re-run 'docker compose run --rm etl catalogs'")


def run() -> None:
    """CLI entry point."""
    # Parse optional range from argv (after the target name)
    args = sys.argv[2:] if len(sys.argv) > 2 else []

    if len(args) == 2:
        start, end = int(args[0]), int(args[1])
        # Full range: scan in smaller chunks to avoid one giant binary search
        pages = [
            (s, min(s + PAGE_WIDTH, end))
            for s in range(start, end, PAGE_WIDTH)
        ]
        # Filter to only pages that actually return 502 before drilling down
        logger.info(
            "Scanning full range [%d, %d) in %d chunks of %d...",
            start,
            end,
            len(pages),
            PAGE_WIDTH,
        )
        with httpx.Client(
            headers={"X-API-Key": settings.microsip_api_key},
            timeout=60.0,
        ) as client:
            bad_pages = []
            for p_start, p_end in pages:
                status = _get_status(client, p_start, limit=p_end - p_start)
                if status != 200:
                    bad_pages.append((p_start, p_end))
            logger.info("Found %d bad page(s): %s", len(bad_pages), bad_pages)

            bad_offsets = _scan_pages(client, bad_pages)
            _report(client, bad_offsets)
    else:
        # Default: use the 5 known bad pages from the previous run
        pages = [(s, s + PAGE_WIDTH) for s in DEFAULT_BAD_PAGES]
        logger.info(
            "Using default bad pages from previous run: %s", DEFAULT_BAD_PAGES
        )
        logger.info("(Pass 'start end' as args to scan a different range)")
        with httpx.Client(
            headers={"X-API-Key": settings.microsip_api_key},
            timeout=60.0,
        ) as client:
            bad_offsets = _scan_pages(client, pages)
            _report(client, bad_offsets)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    run()
