"""CLI entry point for the Microsip ETL pipeline.

    python main.py nightly                       # daily run (alias: all)
    python main.py catalogs|transactions|facts|saldos|snapshots
    python main.py backfill [--from D] [--to D] [--tables a,b]
    python main.py ensure-tables                 # create/reconcile tables
    python main.py views                         # internal views
    python main.py proveedores-views             # per-supplier authorized views
    python main.py reporte-mensual [--mes YYYY-MM] [--corte formato|zona|almacen]
                                   [--salida DIR] [--sin-correo]
"""

from __future__ import annotations

import argparse
import logging
import smtplib
import sys
from datetime import date
from pathlib import Path

from google.api_core.exceptions import GoogleAPIError

from api_client import MicrosipClient
from bq_loader import BigQueryLoader
from config import settings
from pipeline import Pipeline, PipelineError, add_months
from reporte_mensual import CORTE_DEFAULT, CORTES, enviar_por_correo, generar_reporte
from sync_state import SyncStateManager
from views import create_internal_views, create_proveedor_views

TARGETS = (
    "nightly",
    "all",
    "catalogs",
    "transactions",
    "facts",
    "saldos",
    "snapshots",
    "backfill",
    "ensure-tables",
    "views",
    "proveedores-views",
    "reporte-mensual",
)

DEFAULT_REPORTS_DIR = "reports"


def _parse_mes(text: str) -> date:
    """``YYYY-MM`` → first day of that month."""
    try:
        return date.fromisoformat(f"{text.strip()}-01")
    except ValueError:
        raise argparse.ArgumentTypeError(f"--mes debe ser YYYY-MM, no {text!r}") from None


def _default_mes(today: date | None = None) -> date:
    """Previous month (the report runs in the first days of each month)."""
    return add_months(today or date.today(), -1)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Microsip → BigQuery ETL")
    parser.add_argument("target", nargs="?", default="nightly", choices=TARGETS)
    parser.add_argument("--from", dest="start", type=date.fromisoformat, default=None,
                        help="backfill start (YYYY-MM-DD); default resumes or BACKFILL_START_DATE")
    parser.add_argument("--to", dest="end", type=date.fromisoformat, default=None,
                        help="backfill end (YYYY-MM-DD); default today")
    parser.add_argument("--tables", default="",
                        help="comma-separated subset for backfill: catalogs,ventas_documentos,"
                             "fact_ventas_articulo,fact_compras_partidas,fact_saldos_mensuales,"
                             "inventario_existencias")
    report = parser.add_argument_group("reporte-mensual")
    report.add_argument("--mes", type=_parse_mes, default=None,
                        help="mes del reporte (YYYY-MM); default: mes anterior")
    report.add_argument("--corte", choices=tuple(CORTES), default=CORTE_DEFAULT,
                        help="dimensión de la hoja Ventas (default: formato)")
    report.add_argument("--salida", default=DEFAULT_REPORTS_DIR,
                        help=f"directorio del .xlsx (default: {DEFAULT_REPORTS_DIR}/)")
    report.add_argument("--sin-correo", dest="sin_correo", action="store_true",
                        help="no enviar el reporte por correo aunque SMTP esté configurado")
    return parser.parse_args(argv)


def _run_reporte_mensual(args: argparse.Namespace) -> int:
    log = logging.getLogger(__name__)
    mes = args.mes or _default_mes()
    try:
        with BigQueryLoader(
            settings.gcp_project_id, settings.bq_dataset, settings.bq_location
        ) as loader:
            path = generar_reporte(
                loader,
                settings.bq_dataset,
                mes,
                corte=args.corte,
                salida=Path(args.salida),
                empresa=settings.microsip_empresa,
            )
    except (GoogleAPIError, OSError, ValueError) as exc:
        log.error("No se pudo generar el reporte: %s", exc)
        return 1

    if args.sin_correo:
        log.info("Correo omitido (--sin-correo)")
        return 0
    if not settings.smtp_configured:
        log.info("Correo omitido: SMTP_HOST, SMTP_FROM o REPORTE_ISCAM_TO no configurados")
        return 0
    try:
        enviar_por_correo(
            path,
            settings.reporte_iscam_to_list,
            host=settings.smtp_host,
            port=settings.smtp_port,
            user=settings.smtp_user,
            password=settings.smtp_password,
            sender=settings.smtp_from,
            subject=f"Reporte ISCAM {settings.microsip_empresa} {mes:%Y-%m}",
        )
    except (smtplib.SMTPException, OSError) as exc:
        log.error("El reporte se generó en %s pero no se pudo enviar: %s", path, exc)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    target = "nightly" if args.target == "all" else args.target

    if target == "reporte-mensual":
        return _run_reporte_mensual(args)

    with MicrosipClient(
        settings.microsip_api_url,
        settings.microsip_api_key,
        settings.page_size,
        settings.max_pages,
        empresa=settings.microsip_empresa,
        timeout=settings.request_timeout_seconds,
        bulk_page_size=settings.bulk_page_size,
    ) as api, BigQueryLoader(
        settings.gcp_project_id, settings.bq_dataset, settings.bq_location
    ) as loader:
        loader.ensure_dataset()
        loader.ensure_sync_state_table()

        state = SyncStateManager(loader.client, loader.dataset_ref)
        pipeline = Pipeline(
            api,
            loader,
            state,
            empresa=settings.microsip_empresa,
            backfill_start=settings.backfill_start,
            rolling_window_days=settings.rolling_window_days,
            ventas_chunk_days=settings.ventas_chunk_days,
            compras_chunk_days=settings.compras_chunk_days,
            retention_days_facts=settings.retention_days_facts,
            retention_days_snapshots=settings.retention_days_snapshots,
            formatos_venta_csv=settings.formatos_venta_csv,
        )

        try:
            if target == "nightly":
                pipeline.run_nightly()
            elif target == "catalogs":
                pipeline.ensure_tables()
                pipeline.sync_catalogs()
                pipeline._raise_if_failed()
            elif target == "transactions":
                pipeline.ensure_tables()
                pipeline.sync_transactions()
                pipeline._raise_if_failed()
            elif target == "facts":
                pipeline.ensure_tables()
                pipeline.sync_facts()
                pipeline._raise_if_failed()
            elif target == "saldos":
                pipeline.ensure_tables()
                pipeline.sync_saldos()
                pipeline._raise_if_failed()
            elif target == "snapshots":
                pipeline.ensure_tables()
                pipeline.sync_snapshots()
                pipeline._raise_if_failed()
            elif target == "backfill":
                tables = [t.strip() for t in args.tables.split(",") if t.strip()]
                pipeline.run_backfill(args.start, args.end, tables or None)
            elif target == "ensure-tables":
                pipeline.ensure_tables()
            elif target == "views":
                create_internal_views(loader, settings.bq_dataset)
            elif target == "proveedores-views":
                loader.ensure_dataset(settings.bq_dataset_proveedores)
                create_proveedor_views(
                    loader,
                    settings.bq_dataset,
                    settings.bq_dataset_proveedores,
                    settings.proveedores_bi_ids,
                )
        except PipelineError as exc:
            logging.getLogger(__name__).error("%s", exc)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
