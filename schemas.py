"""Table definitions for the ETL pipeline.

Each table config defines: BigQuery table name, API endpoint, sync strategy,
and (for incremental tables) the primary key and partition/clustering fields.

BigQuery schemas use autodetect=True since most API endpoints return SELECT *,
so exact columns depend on the Microsip version installed.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TableConfig:
    """Configuration for a single ETL table."""

    bq_table: str
    endpoint: str
    strategy: str  # "full_refresh" or "incremental"
    primary_key: str = ""
    partition_field: str = ""
    clustering_fields: list[str] = field(default_factory=list)
    api_params: dict = field(default_factory=dict)


# --- Catalog tables (full refresh) ---

CATALOGS: list[TableConfig] = [
    TableConfig(
        bq_table="dim_clientes",
        endpoint="/clientes",
        strategy="full_refresh",
    ),
    TableConfig(
        bq_table="dim_articulos",
        endpoint="/articulos",
        strategy="full_refresh",
    ),
    TableConfig(
        bq_table="dim_almacenes",
        endpoint="/catalogos/almacenes",
        strategy="full_refresh",
    ),
    TableConfig(
        bq_table="dim_vendedores",
        endpoint="/catalogos/vendedores",
        strategy="full_refresh",
    ),
    TableConfig(
        bq_table="dim_lineas",
        endpoint="/catalogos/lineas",
        strategy="full_refresh",
    ),
]

# --- Transaction tables (incremental by date) ---

# All DOCTOS_VE types load into a single table
VENTAS_ENDPOINTS = [
    ("/ventas/facturas", "F"),
    ("/ventas/remisiones", "R"),
    ("/ventas/pedidos", "P"),
    ("/ventas/cotizaciones", "C"),
    ("/ventas/devoluciones", "D"),
]

VENTAS_DOCUMENTOS = TableConfig(
    bq_table="ventas_documentos",
    endpoint="",  # multiple endpoints, handled specially in pipeline
    strategy="incremental",
    primary_key="DOCTO_VE_ID",
    partition_field="FECHA",
    clustering_fields=["TIPO_DOCTO", "CLIENTE_ID"],
)

PV_TICKETS = TableConfig(
    bq_table="pv_tickets",
    endpoint="/ventas/pv",
    strategy="incremental",
    primary_key="DOCTO_PV_ID",
    partition_field="FECHA",
    clustering_fields=["CAJA_ID"],
)

TRANSACTIONS: list[TableConfig] = [VENTAS_DOCUMENTOS, PV_TICKETS]

# --- Snapshot tables (full refresh) ---

EXISTENCIAS = TableConfig(
    bq_table="inventario_existencias",
    endpoint="/inventarios/existencias",
    strategy="full_refresh",
    api_params={"solo_con_existencia": "true"},
)

SNAPSHOTS: list[TableConfig] = [EXISTENCIAS]

# All tables for convenience
ALL_TABLES: list[TableConfig] = CATALOGS + TRANSACTIONS + SNAPSHOTS
