"""CLI entry point for the Microsip ETL pipeline."""

from __future__ import annotations

import logging
import sys

from api_client import MicrosipClient
from bq_loader import BigQueryLoader
from config import settings
from pipeline import Pipeline
from sync_state import SyncStateManager

TARGETS = ("all", "catalogs", "transactions", "snapshots")


def main():
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    target = sys.argv[1] if len(sys.argv) > 1 else "all"
    if target not in TARGETS:
        print(f"Usage: python main.py [{' | '.join(TARGETS)}]")
        sys.exit(1)

    with MicrosipClient(
        settings.microsip_api_url,
        settings.microsip_api_key,
        settings.page_size,
        settings.max_pages,
    ) as api, BigQueryLoader(
        settings.gcp_project_id, settings.bq_dataset, settings.bq_location
    ) as loader:
        loader.ensure_dataset()
        loader.ensure_sync_state_table()

        state = SyncStateManager(loader.client, loader.dataset_ref)
        pipeline = Pipeline(api, loader, state, settings.initial_lookback_days)

        if target == "all":
            pipeline.run_all()
        elif target == "catalogs":
            pipeline.sync_catalogs()
        elif target == "transactions":
            pipeline.sync_transactions()
        elif target == "snapshots":
            pipeline.sync_snapshots()


if __name__ == "__main__":
    main()
