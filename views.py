"""BigQuery views for Looker Studio.

- ``sql/views/*.sql``: internal views in the main dataset (costs, margins,
  customers). Placeholders: ``{project}``, ``{dataset}``.
- ``sql/proveedores/*.sql``: templates instantiated once per supplier in the
  suppliers dataset, without cost, customer or price-unit data. Placeholders:
  ``{project}``, ``{dataset}``, ``{dataset_proveedores}``, ``{proveedor_id}``.
  Each generated view is registered as an *authorized view* on the main
  dataset so a supplier can be granted access to their views only.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from google.cloud import bigquery

from bq_loader import BigQueryLoader

logger = logging.getLogger(__name__)

SQL_DIR = Path(__file__).parent / "sql"
_VIEW_NAME = re.compile(r"CREATE OR REPLACE VIEW\s+`[^`]*\.([A-Za-z0-9_]+)`", re.IGNORECASE)


def _render(template: str, **values: str) -> str:
    for key, value in values.items():
        template = template.replace("{" + key + "}", value)
    return template


def _sql_files(folder: Path) -> list[Path]:
    files = sorted(folder.glob("*.sql"))
    if not files:
        raise FileNotFoundError(f"No .sql files in {folder}")
    return files


def create_internal_views(loader: BigQueryLoader, dataset: str) -> list[str]:
    """Create/replace every view in sql/views. Returns the view names."""
    created: list[str] = []
    for path in _sql_files(SQL_DIR / "views"):
        sql = _render(path.read_text(encoding="utf-8"), project=loader.project_id, dataset=dataset)
        loader.execute(sql)
        name = _VIEW_NAME.search(sql)
        created.append(name.group(1) if name else path.stem)
        logger.info("View %s ready", created[-1])
    return created


def create_proveedor_views(
    loader: BigQueryLoader,
    dataset: str,
    dataset_proveedores: str,
    proveedor_ids: list[int],
) -> list[str]:
    """Instantiate the supplier templates for each id and authorize them."""
    if not proveedor_ids:
        logger.warning("PROVEEDORES_BI is empty; no supplier views created")
        return []

    created: list[str] = []
    templates = _sql_files(SQL_DIR / "proveedores")
    for pid in proveedor_ids:
        for path in templates:
            sql = _render(
                path.read_text(encoding="utf-8"),
                project=loader.project_id,
                dataset=dataset,
                dataset_proveedores=dataset_proveedores,
                proveedor_id=str(int(pid)),
            )
            loader.execute(sql)
            name = _VIEW_NAME.search(sql)
            view = name.group(1) if name else f"{path.stem}_{pid}"
            created.append(view)
            logger.info("Supplier view %s ready", view)

    _authorize_views(loader, dataset, dataset_proveedores, created)
    return created


def _authorize_views(
    loader: BigQueryLoader, source_dataset: str, view_dataset: str, views: list[str]
):
    """Register views as authorized views on the source dataset."""
    client = loader.client
    ds = client.get_dataset(f"{loader.project_id}.{source_dataset}")
    entries = list(ds.access_entries)
    existing = {
        (e.entity_id.get("datasetId"), e.entity_id.get("tableId"))
        for e in entries
        if e.entity_type == "view" and isinstance(e.entity_id, dict)
    }
    added = 0
    for view in views:
        if (view_dataset, view) in existing:
            continue
        entries.append(
            bigquery.AccessEntry(
                role=None,
                entity_type="view",
                entity_id={
                    "projectId": loader.project_id,
                    "datasetId": view_dataset,
                    "tableId": view,
                },
            )
        )
        added += 1
    if added:
        ds.access_entries = entries
        client.update_dataset(ds, ["access_entries"])
    logger.info("Authorized %d new views on %s", added, source_dataset)
