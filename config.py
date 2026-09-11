from __future__ import annotations

import logging
import warnings
from datetime import date

from pydantic import field_validator
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    # Microsip API
    microsip_api_url: str = "http://localhost:8000/api/v1"
    microsip_api_key: str = ""
    # Sent as the X-Empresa header and written to every row as EMPRESA.
    microsip_empresa: str = "ALMACENES PACHECO"

    # Google Cloud
    gcp_project_id: str = ""
    bq_dataset: str = "microsip"
    bq_dataset_proveedores: str = "microsip_proveedores"
    bq_location: str = "us-central1"

    # ETL behavior
    page_size: int = 500  # offset pagination (legacy endpoints)
    bulk_page_size: int = 5000  # keyset pagination (/etl endpoints)
    max_pages: int = 10_000  # safety valve against infinite pagination
    request_timeout_seconds: float = 180.0  # /etl/ventas-articulo takes ~20 s per week
    initial_lookback_days: int = 30  # legacy: ventas_documentos first run
    log_level: str = "INFO"

    # History window. Empty = today minus `backfill_years`.
    backfill_start_date: str = ""
    backfill_years: int = 3
    # Nightly runs recompute this many days back (cancellations, late returns).
    rolling_window_days: int = 45
    # Chunk sizes for backfill (days). ventas-articulo runs a stored procedure
    # per call (~19 s / 7 days), compras is a plain keyset scan.
    ventas_chunk_days: int = 7
    compras_chunk_days: int = 31

    # Retention (BigQuery partition expiration). Fact tables roll 3 years;
    # daily inventory snapshots keep ~13 months. Monthly balances never expire
    # because the opening balance row would be lost.
    retention_days_facts: int = 1100
    retention_days_snapshots: int = 400

    # Comma-separated PROVEEDOR_IDs that get their own authorized views in
    # `bq_dataset_proveedores`. Empty = none.
    proveedores_bi: str = ""

    # Sales-format mapping (regex over TIPOS_CLIENTES.NOMBRE) loaded into
    # dim_formato_venta on every catalog run. Relative paths resolve from the
    # repo root. Edit the CSV to change the mapping; no code change needed.
    formatos_venta_csv: str = "config/formatos_venta.csv"

    # Monthly ISCAM report e-mail (optional). The report is sent only when
    # smtp_host, smtp_from and reporte_iscam_to are all set.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    reporte_iscam_to: str = ""  # comma-separated recipients

    # extra=ignore: .env may carry variables for other tools (e.g.
    # GOOGLE_APPLICATION_CREDENTIALS, read by the BigQuery client itself).
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    @field_validator("microsip_api_key")
    @classmethod
    def api_key_must_not_be_empty(cls, v: str) -> str:
        if not v or v.strip() == "" or v == "your-api-key-here":
            raise ValueError(
                "MICROSIP_API_KEY is required. "
                "Set it in .env or as an environment variable."
            )
        return v

    @field_validator("gcp_project_id")
    @classmethod
    def gcp_project_must_not_be_empty(cls, v: str) -> str:
        if not v or v.strip() == "":
            raise ValueError(
                "GCP_PROJECT_ID is required. "
                "Set it in .env or as an environment variable."
            )
        return v

    @field_validator("microsip_api_url")
    @classmethod
    def warn_if_http(cls, v: str) -> str:
        if v.startswith("http://") and "localhost" not in v and "127.0.0.1" not in v:
            warnings.warn(
                f"MICROSIP_API_URL uses plain HTTP ({v}). "
                "API key will be sent in cleartext. Use HTTPS in production.",
                UserWarning,
                stacklevel=2,
            )
        return v

    @field_validator("backfill_start_date")
    @classmethod
    def backfill_start_must_be_iso(cls, v: str) -> str:
        if v:
            date.fromisoformat(v)  # raises ValueError on bad format
        return v

    @property
    def backfill_start(self) -> date:
        """First day of history to load."""
        if self.backfill_start_date:
            return date.fromisoformat(self.backfill_start_date)
        today = date.today()
        try:
            return today.replace(year=today.year - self.backfill_years)
        except ValueError:  # Feb 29
            return today.replace(year=today.year - self.backfill_years, day=28)

    @property
    def proveedores_bi_ids(self) -> list[int]:
        return [int(p) for p in self.proveedores_bi.split(",") if p.strip()]

    @property
    def reporte_iscam_to_list(self) -> list[str]:
        return [a.strip() for a in self.reporte_iscam_to.split(",") if a.strip()]

    @property
    def smtp_configured(self) -> bool:
        """True when the report can be e-mailed."""
        return bool(self.smtp_host.strip() and self.smtp_from.strip() and self.reporte_iscam_to_list)


settings = Settings()
