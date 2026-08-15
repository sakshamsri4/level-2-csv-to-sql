"""Every value here is read from the environment. Nothing in .env.example is decorative."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: str = ""
    assay_generation_model: str = "gpt-4o-mini"
    assay_raw_dir: Path = Path("data/raw")
    assay_warehouse: Path = Path("data/warehouse/shipments.duckdb")
    assay_max_rows: int = 200


@lru_cache
def settings() -> Settings:
    return Settings()
