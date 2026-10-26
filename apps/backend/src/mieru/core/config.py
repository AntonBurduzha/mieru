from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, Field, PostgresDsn, RedisDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Every backend setting, validated once at startup."""

    model_config = SettingsConfigDict(
        env_file="../../.env",
        env_ignore_empty=True,
        extra="ignore",
    )
    env: Literal["local", "test", "prod"] = "local"
    app_base_url: AnyHttpUrl = AnyHttpUrl("http://localhost:3000")
    ingest_base_url: AnyHttpUrl = AnyHttpUrl("http://localhost:8001")

    database_url: PostgresDsn
    redis_url: RedisDsn
    clickhouse_url: AnyHttpUrl

    session_ttl_days: int = Field(default=30, gt=1)
    session_cookie_secure: bool = False
    geoip_db_path: Path = Path("data/dbip-country-lite.mmdb")

    writer_batch_max: int = Field(default=5000, gt=0)
    writer_flush_ms: int = Field(default=1000, gt=0)
    stream_high_water: int = Field(default=200_000, gt=0)
    ingest_fallback_queue: int = Field(default=10_000, ge=0)
    loadtest_secret: SecretStr | None = None

    stripe_secret_key: SecretStr | None = None
    stripe_webhook_secret: SecretStr | None = None
    stripe_price_starter: str | None = None
    stripe_price_pro: str | None = None
    resend_api_key: SecretStr | None = None
    email_from: str = "Mieru <hello@mieru.example>"

    otel_exporter_otlp_endpoint: AnyHttpUrl = AnyHttpUrl("http://localhost:4317")
    otel_service_name: str | None = None
    otel_traces_sampler_ratio: float = Field(default=0.1, ge=0.0, le=1.0)
    git_sha: str = "dev"
    mieru_version: str | None = None
    writer_test_crash_after_insert: bool = False


@lru_cache
def get_settings() -> Settings:
    """Build Settings per process"""
    return Settings()
