from __future__ import annotations

import logging
import warnings

from pydantic import field_validator
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    # Microsip API
    microsip_api_url: str = "http://localhost:8000/api/v1"
    microsip_api_key: str = ""

    # Google Cloud
    gcp_project_id: str = ""
    bq_dataset: str = "microsip"
    bq_location: str = "us-central1"

    # ETL behavior
    page_size: int = 500
    max_pages: int = 10_000  # safety valve against infinite pagination
    initial_lookback_days: int = 30
    log_level: str = "INFO"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

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


settings = Settings()
