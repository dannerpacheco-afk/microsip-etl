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
    response = client.get(
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
