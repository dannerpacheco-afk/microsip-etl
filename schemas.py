"""Table definitions for the ETL pipeline.

Each TableConfig describes one BigQuery table: which API endpoint feeds it,
how it is fetched (offset / keyset / chunk), how it is loaded, and its
explicit BigQuery schema when it is a fact table.

Catalog (dim_*) tables still use autodetect: they are small, fully
refreshed every run, and their columns depend on the Microsip version.
Fact tables use explicit schemas so MERGE / DELETE+INSERT never break on a
type drift and NUMERIC values coming as strings from the API load correctly.
``dim_articulo_claves`` and ``dim_formato_venta`` also use explicit schemas
because views depend on their column types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from google.cloud.bigquery import SchemaField

# --- Fetch strategies ---
FETCH_OFFSET = "offset"  # legacy skip/limit endpoints
FETCH_KEYSET = "keyset"  # /etl endpoints with cursor/next_cursor
FETCH_CHUNK = "chunk"  # /etl endpoints that return the whole chunk
FETCH_CSV = "csv"  # local CSV under config/ (no API call)

# --- Load strategies ---
LOAD_FULL_REFRESH = "full_refresh"  # WRITE_TRUNCATE
LOAD_MERGE = "merge"  # staging + MERGE on primary key
LOAD_REPLACE_RANGE = "replace_range"  # DELETE date range + INSERT
LOAD_REPLACE_MONTHS = "replace_months"  # DELETE periods + INSERT
LOAD_SNAPSHOT = "snapshot"  # WRITE_TRUNCATE on today's partition


@dataclass
class TableConfig:
    """Configuration for a single ETL table."""

    bq_table: str
    endpoint: str
    fetch: str = FETCH_OFFSET
    load: str = LOAD_FULL_REFRESH
    primary_key: str = ""
    partition_field: str = ""
    partition_type: str = "DAY"  # DAY | MONTH
    clustering_fields: list[str] = field(default_factory=list)
    api_params: dict = field(default_factory=dict)
    schema: list[SchemaField] | None = None
    expiration_days: int | None = None  # partition expiration; None = keep
    chunk_days: int = 7  # backfill chunk for date-ranged endpoints


# --- Common metadata columns (added to every row by the pipeline) ---

META_FIELDS = [
    SchemaField("EMPRESA", "STRING", mode="REQUIRED"),
    SchemaField("_synced_at", "TIMESTAMP", mode="REQUIRED"),
]


def _s(name: str, type_: str, mode: str = "NULLABLE") -> SchemaField:
    return SchemaField(name, type_, mode=mode)


# --- Catalog tables (full refresh) ---
# Explicit schemas with only the columns the views use. Autodetect broke on
# dim_articulos: CUENTA_COSTO_VENTA was inferred INTEGER from "5100" and then
# hit "5100.2". Unknown columns from the API are dropped (coerce_row +
# ignore_unknown_values).

CATALOGS: list[TableConfig] = [
    TableConfig(
        bq_table="dim_clientes",
        endpoint="/clientes",
        schema=[
            _s("CLIENTE_ID", "INT64", "REQUIRED"),
            _s("NOMBRE", "STRING"),
            _s("ESTATUS", "STRING"),
            _s("CAUSA_SUSP", "STRING"),
            _s("FECHA_SUSP", "DATE"),
            _s("VENDEDOR_ID", "INT64"),
            _s("COBRADOR_ID", "INT64"),
            _s("TIPO_CLIENTE_ID", "INT64"),
            _s("ZONA_CLIENTE_ID", "INT64"),
            _s("COND_PAGO_ID", "INT64"),
            _s("MONEDA_ID", "INT64"),
            _s("PRECIO_EMPRESA_ID", "INT64"),
            _s("LIMITE_CREDITO", "NUMERIC"),
            _s("CONTACTO1", "STRING"),
            _s("FECHA_HORA_CREACION", "DATETIME"),
            _s("FECHA_HORA_ULT_MODIF", "DATETIME"),
            *META_FIELDS,
        ],
    ),
    TableConfig(
        bq_table="dim_articulos",
        endpoint="/articulos",
        schema=[
            _s("ARTICULO_ID", "INT64", "REQUIRED"),
            _s("NOMBRE", "STRING"),
            _s("ESTATUS", "STRING"),
            _s("CAUSA_SUSP", "STRING"),
            _s("FECHA_SUSP", "DATE"),
            _s("LINEA_ARTICULO_ID", "INT64"),
            _s("UNIDAD_VENTA", "STRING"),
            _s("UNIDAD_COMPRA", "STRING"),
            _s("CONTENIDO_UNIDAD_COMPRA", "NUMERIC"),
            _s("PESO_UNITARIO", "NUMERIC"),
            _s("ES_ALMACENABLE", "STRING"),
            _s("ES_JUEGO", "STRING"),
            _s("ES_IMPORTADO", "STRING"),
            _s("ES_PRECIO_VARIABLE", "STRING"),
            _s("DIAS_GARANTIA", "INT64"),
            _s("FECHA_HORA_CREACION", "DATETIME"),
            _s("FECHA_HORA_ULT_MODIF", "DATETIME"),
            *META_FIELDS,
        ],
    ),
    TableConfig(
        bq_table="dim_proveedores",
        endpoint="/proveedores",
        schema=[
            _s("PROVEEDOR_ID", "INT64", "REQUIRED"),
            _s("NOMBRE", "STRING"),
            _s("ESTATUS", "STRING"),
            _s("RFC_CURP", "STRING"),
            _s("TIPO_PROV_ID", "INT64"),
            _s("COND_PAGO_ID", "INT64"),
            _s("MONEDA_ID", "INT64"),
            _s("CIUDAD_ID", "INT64"),
            _s("ESTADO_ID", "INT64"),
            _s("PAIS_ID", "INT64"),
            _s("LIMITE_CREDITO", "NUMERIC"),
            _s("EMAIL", "STRING"),
            _s("TELEFONO1", "STRING"),
            _s("CONTACTO1", "STRING"),
            _s("EXTRANJERO", "STRING"),
            _s("FECHA_HORA_CREACION", "DATETIME"),
            _s("FECHA_HORA_ULT_MODIF", "DATETIME"),
            *META_FIELDS,
        ],
    ),
    TableConfig(
        bq_table="dim_almacenes",
        endpoint="/catalogos/almacenes",
        schema=[
            _s("ALMACEN_ID", "INT64", "REQUIRED"),
            _s("NOMBRE", "STRING"),
            _s("NOMBRE_ABREV", "STRING"),
            _s("ES_PPAL", "STRING"),
            _s("ES_PREDET", "STRING"),
            _s("OCULTO", "STRING"),
            _s("POBLACION", "STRING"),
            _s("CIUDAD", "STRING"),
            _s("ESTADO", "STRING"),
            *META_FIELDS,
        ],
    ),
    TableConfig(
        bq_table="dim_vendedores",
        endpoint="/catalogos/vendedores",
        schema=[
            _s("VENDEDOR_ID", "INT64", "REQUIRED"),
            _s("NOMBRE", "STRING"),
            _s("ES_PREDET", "STRING"),
            _s("OCULTO", "STRING"),
            *META_FIELDS,
        ],
    ),
    TableConfig(
        bq_table="dim_lineas",
        endpoint="/catalogos/lineas",
        schema=[
            _s("LINEA_ARTICULO_ID", "INT64", "REQUIRED"),
            _s("NOMBRE", "STRING"),
            _s("CLAVE", "STRING"),
            _s("GRUPO_LINEA_ID", "INT64"),
            _s("ES_PREDET", "STRING"),
            _s("OCULTO", "STRING"),
            *META_FIELDS,
        ],
    ),
]

DIM_ARTICULO_PROVEEDOR = TableConfig(
    bq_table="dim_articulo_proveedor",
    endpoint="/etl/articulos-proveedores",
    fetch=FETCH_KEYSET,
    load=LOAD_FULL_REFRESH,
    schema=[
        _s("PRECIO_COMPRA_ID", "INT64", "REQUIRED"),
        _s("ARTICULO_ID", "INT64", "REQUIRED"),
        _s("PROVEEDOR_ID", "INT64", "REQUIRED"),
        _s("ES_PROV_PREDET", "BOOL"),
        _s("PRIORIDAD_COMPRA", "STRING"),
        _s("CLAVE_ART_PROV", "STRING"),
        _s("UNIDAD_COMPRA", "STRING"),
        _s("CONTENIDO_UNIDAD_COMPRA", "NUMERIC"),
        _s("UNIDADES_MIN_UCOM", "NUMERIC"),
        _s("FECHA_PRECIO_ULT_COMPRA", "DATE"),
        _s("DIAS_ENTREGA_PROM", "NUMERIC"),
        _s("TOT_DIAS_ENTREGA", "NUMERIC"),
        _s("NUM_COMPRAS", "INT64"),
        _s("FECHA_HORA_ULT_MODIF", "DATETIME"),
        *META_FIELDS,
    ],
)

# Claves de artículo (CLAVES_ARTICULOS): clave principal, alternas, SKU.
# Los códigos de barras viven como "Clave alterna" de 12-14 dígitos.
DIM_ARTICULO_CLAVES = TableConfig(
    bq_table="dim_articulo_claves",
    endpoint="/etl/claves-articulos",
    fetch=FETCH_KEYSET,
    load=LOAD_FULL_REFRESH,
    clustering_fields=["ARTICULO_ID"],
    schema=[
        _s("CLAVE_ARTICULO_ID", "INT64", "REQUIRED"),
        _s("ARTICULO_ID", "INT64", "REQUIRED"),
        _s("ROL_CLAVE_ART_ID", "INT64"),
        _s("ROL", "STRING"),
        _s("ES_PPAL", "STRING"),
        _s("ES_GTIN", "STRING"),
        _s("CLAVE_ARTICULO", "STRING"),
        _s("CONTENIDO_EMPAQUE", "NUMERIC"),
        *META_FIELDS,
    ],
)

# Small auxiliary catalogs served whole by /etl/catalogos-aux?tabla=<name>
# (no pagination). Autodetect: columns depend on the Microsip version.
CATALOGOS_AUX_ENDPOINT = "/etl/catalogos-aux"


def _catalogo_aux(bq_table: str, tabla: str) -> TableConfig:
    return TableConfig(
        bq_table=bq_table,
        endpoint=CATALOGOS_AUX_ENDPOINT,
        fetch=FETCH_CHUNK,
        load=LOAD_FULL_REFRESH,
        api_params={"tabla": tabla},
    )


# In this company TIPOS_CLIENTES is the sales route / zone catalog.
CATALOGOS_AUX: list[TableConfig] = [
    _catalogo_aux("dim_tipos_clientes", "tipos_clientes"),
    _catalogo_aux("dim_zonas_clientes", "zonas_clientes"),
    _catalogo_aux("dim_sucursales", "sucursales"),
    _catalogo_aux("dim_precios_empresa", "precios_empresa"),
]

# Sales-format mapping maintained by the user in config/formatos_venta.csv:
# PATRON is an RE2 regex matched (case-insensitively) against
# dim_tipos_clientes.NOMBRE; the lowest ORDEN that matches wins.
DIM_FORMATO_VENTA = TableConfig(
    bq_table="dim_formato_venta",
    endpoint="",  # local CSV, see pipeline.load_formatos_venta
    fetch=FETCH_CSV,
    load=LOAD_FULL_REFRESH,
    schema=[
        _s("ORDEN", "INT64", "REQUIRED"),
        _s("PATRON", "STRING", "REQUIRED"),
        _s("FORMATO", "STRING", "REQUIRED"),
        _s("INCLUIR", "BOOL", "REQUIRED"),
        *META_FIELDS,
    ],
)

# --- Sales document headers (legacy endpoints, MERGE on DOCTO_VE_ID) ---

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
    fetch=FETCH_OFFSET,
    load=LOAD_MERGE,
    primary_key="DOCTO_VE_ID",
    partition_field="FECHA",
    clustering_fields=["TIPO_DOCTO", "CLIENTE_ID"],
    chunk_days=31,
    # Explicit: autodetect inferred BOOL for 'S'/'N' flags (ACREDITAR_CXC)
    # and broke mid-load. Only the columns the BI uses.
    schema=[
        _s("DOCTO_VE_ID", "INT64", "REQUIRED"),
        _s("TIPO_DOCTO", "STRING"),
        _s("SUBTIPO_DOCTO", "STRING"),
        _s("SUCURSAL_ID", "INT64"),
        _s("FOLIO", "STRING"),
        _s("FECHA", "DATE", "REQUIRED"),
        _s("HORA", "STRING"),
        _s("CLIENTE_ID", "INT64"),
        _s("CLAVE_CLIENTE", "STRING"),
        _s("CLIENTE_NOMBRE", "STRING"),
        _s("DIR_CLI_ID", "INT64"),
        _s("ALMACEN_ID", "INT64"),
        _s("VENDEDOR_ID", "INT64"),
        _s("MONEDA_ID", "INT64"),
        _s("TIPO_CAMBIO", "NUMERIC"),
        _s("ESTATUS", "STRING"),
        _s("APLICADO", "STRING"),
        _s("TIPO_DSCTO", "STRING"),
        _s("DSCTO_PCTJE", "NUMERIC"),
        _s("DSCTO_IMPORTE", "NUMERIC"),
        _s("IMPORTE_NETO", "NUMERIC"),
        _s("FLETES", "NUMERIC"),
        _s("OTROS_CARGOS", "NUMERIC"),
        _s("TOTAL_IMPUESTOS", "NUMERIC"),
        _s("TOTAL_RETENCIONES", "NUMERIC"),
        _s("TOTAL_ANTICIPOS", "NUMERIC"),
        _s("IMPORTE_COBRO", "NUMERIC"),
        _s("COND_PAGO_ID", "INT64"),
        _s("VIA_EMBARQUE_ID", "INT64"),
        _s("PCTJE_COMIS", "NUMERIC"),
        _s("ORDEN_COMPRA", "STRING"),
        _s("FECHA_ORDEN_COMPRA", "DATE"),
        _s("DESCRIPCION", "STRING"),
        _s("SISTEMA_ORIGEN", "STRING"),
        _s("FORMA_EMITIDA", "STRING"),
        _s("ES_CFD", "STRING"),
        _s("METODO_PAGO_SAT", "STRING"),
        _s("USO_CFDI", "STRING"),
        _s("CFDI_FACT_DEVUELTA_ID", "INT64"),
        _s("USUARIO_CREADOR", "STRING"),
        _s("FECHA_HORA_CREACION", "DATETIME"),
        _s("USUARIO_ULT_MODIF", "STRING"),
        _s("FECHA_HORA_ULT_MODIF", "DATETIME"),
        _s("USUARIO_CANCELACION", "STRING"),
        _s("FECHA_HORA_CANCELACION", "DATETIME"),
        *META_FIELDS,
    ],
)

# --- Fact tables (/etl endpoints, explicit schemas) ---

FACT_VENTAS_ARTICULO = TableConfig(
    bq_table="fact_ventas_articulo",
    endpoint="/etl/ventas-articulo",
    fetch=FETCH_CHUNK,
    load=LOAD_REPLACE_RANGE,
    partition_field="FECHA",
    clustering_fields=["ARTICULO_ID", "ALMACEN_ID"],
    chunk_days=7,
    schema=[
        _s("DOCTO_VE_ID", "INT64", "REQUIRED"),
        _s("TIPO_DOCTO", "STRING"),
        _s("FOLIO", "STRING"),
        _s("FECHA", "DATE", "REQUIRED"),
        _s("ESTATUS", "STRING"),
        _s("CLIENTE_ID", "INT64"),
        _s("VENDEDOR_ID", "INT64"),
        _s("SUCURSAL_ID", "INT64"),
        _s("ALMACEN_ID", "INT64", "REQUIRED"),
        _s("ARTICULO_ID", "INT64", "REQUIRED"),
        _s("SIGNO", "NUMERIC"),
        _s("UNIDADES", "NUMERIC"),
        _s("IMPORTE_NETO", "NUMERIC"),
        _s("COSTO", "NUMERIC"),
        _s("UTILIDAD", "NUMERIC"),
        _s("IMPUESTOS", "NUMERIC"),  # IVA + IEPS por docto x articulo, con SIGNO
        _s("IMPORTE_TOTAL", "NUMERIC"),  # IMPORTE_NETO + IMPUESTOS
        _s("MONEDA_ID", "INT64"),
        _s("TIPO_CAMBIO", "NUMERIC"),
        _s("FECHA_HORA_ULT_MODIF", "DATETIME"),
        *META_FIELDS,
    ],
)

FACT_COMPRAS_PARTIDAS = TableConfig(
    bq_table="fact_compras_partidas",
    endpoint="/etl/compras-partidas",
    fetch=FETCH_KEYSET,
    load=LOAD_REPLACE_RANGE,
    partition_field="FECHA",
    clustering_fields=["PROVEEDOR_ID", "ARTICULO_ID"],
    chunk_days=31,
    schema=[
        _s("DOCTO_CM_DET_ID", "INT64", "REQUIRED"),
        _s("DOCTO_CM_ID", "INT64", "REQUIRED"),
        _s("TIPO_DOCTO", "STRING"),
        _s("FOLIO", "STRING"),
        _s("FECHA", "DATE", "REQUIRED"),
        _s("ESTATUS", "STRING"),
        _s("APLICADO", "STRING"),
        _s("PROVEEDOR_ID", "INT64"),
        _s("ALMACEN_ID", "INT64"),
        _s("SUCURSAL_ID", "INT64"),
        _s("FOLIO_PROV", "STRING"),
        _s("MONEDA_ID", "INT64"),
        _s("TIPO_CAMBIO", "NUMERIC"),
        _s("FECHA_HORA_ULT_MODIF", "DATETIME"),
        _s("ARTICULO_ID", "INT64"),
        _s("CLAVE_ARTICULO", "STRING"),
        _s("POSICION", "INT64"),
        _s("UMED", "STRING"),
        _s("CONTENIDO_UMED", "NUMERIC"),
        _s("UNIDADES", "NUMERIC"),
        _s("UNIDADES_A_REC", "NUMERIC"),
        _s("UNIDADES_REC_DEV", "NUMERIC"),
        _s("PRECIO_UNITARIO", "NUMERIC"),
        _s("PRECIO_TOTAL_NETO", "NUMERIC"),
        _s("PCTJE_DSCTO", "NUMERIC"),
        _s("DSCTO_ART", "NUMERIC"),
        _s("DSCTO_EXTRA", "NUMERIC"),
        *META_FIELDS,
    ],
)

FACT_SALDOS_MENSUALES = TableConfig(
    bq_table="fact_saldos_mensuales",
    endpoint="/etl/saldos-mensuales",
    fetch=FETCH_CHUNK,
    load=LOAD_REPLACE_MONTHS,
    partition_field="PERIODO",
    partition_type="MONTH",
    clustering_fields=["ARTICULO_ID", "ALMACEN_ID"],
    expiration_days=None,  # never: the opening balance row must survive
    schema=[
        _s("PERIODO", "DATE", "REQUIRED"),  # first day of the month
        _s("ANO", "INT64", "REQUIRED"),
        _s("MES", "INT64", "REQUIRED"),
        _s("ULTIMO_DIA", "INT64"),
        _s("ARTICULO_ID", "INT64", "REQUIRED"),
        _s("ALMACEN_ID", "INT64", "REQUIRED"),
        _s("ENTRADAS_UNIDADES", "NUMERIC"),
        _s("SALIDAS_UNIDADES", "NUMERIC"),
        _s("ENTRADAS_COSTO", "NUMERIC"),
        _s("SALIDAS_COSTO", "NUMERIC"),
        _s("ES_SALDO_INICIAL", "BOOL", "REQUIRED"),
        *META_FIELDS,
    ],
)

INVENTARIO_EXISTENCIAS = TableConfig(
    bq_table="inventario_existencias",
    endpoint="/etl/saldos-iniciales",  # cumulative balance up to current month
    fetch=FETCH_CHUNK,
    load=LOAD_SNAPSHOT,
    partition_field="_snapshot_date",
    clustering_fields=["ALMACEN_ID"],
    schema=[
        _s("_snapshot_date", "DATE", "REQUIRED"),
        _s("ARTICULO_ID", "INT64", "REQUIRED"),
        _s("ALMACEN_ID", "INT64", "REQUIRED"),
        _s("EXISTENCIA", "NUMERIC"),
        _s("COSTO", "NUMERIC"),
        *META_FIELDS,
    ],
)

FACTS: list[TableConfig] = [FACT_VENTAS_ARTICULO, FACT_COMPRAS_PARTIDAS]
ALL_TABLES: list[TableConfig] = (
    CATALOGS
    + CATALOGOS_AUX
    + [DIM_ARTICULO_PROVEEDOR, DIM_ARTICULO_CLAVES, DIM_FORMATO_VENTA, VENTAS_DOCUMENTOS]
    + FACTS
    + [FACT_SALDOS_MENSUALES, INVENTARIO_EXISTENCIAS]
)

# Tables from the v1 pipeline that no longer exist. Listed so operators know
# they can be dropped manually (DOCTOS_PV is empty in every company).
DEPRECATED_TABLES = ["pv_tickets"]


# --- Row coercion for explicit schemas ---

_DATETIME_TYPES = {"DATETIME", "TIMESTAMP"}


def coerce_row(row: dict, schema: list[SchemaField]) -> dict:
    """Return a copy of ``row`` with only schema columns, normalized for BQ.

    - Unknown keys are dropped (the API may add columns over time).
    - Empty strings become NULL.
    - DATETIME values use a space separator ("YYYY-MM-DD HH:MM:SS").
    - NUMERIC/INT64 values that arrive as strings are kept as strings; the
      BigQuery JSON loader parses them, which avoids float rounding.
    """
    out: dict = {}
    for f in schema:
        value = row.get(f.name)
        if isinstance(value, str) and f.field_type == "STRING":
            value = value.strip()  # CHAR(n) columns arrive space-padded
        if value == "":
            value = None
        if value is not None and f.field_type in _DATETIME_TYPES:
            if isinstance(value, datetime):
                value = value.isoformat(sep=" ")
            elif isinstance(value, str) and f.field_type == "DATETIME":
                value = value.replace("T", " ")
        elif value is not None and f.field_type == "DATE" and isinstance(value, (datetime, date)):
            value = value.isoformat()[:10]
        out[f.name] = value
    return out
