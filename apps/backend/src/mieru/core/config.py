from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import AnyHttpUrl, Field, PostgresDsn, RedisDsn, Secret, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Marks the repo root; the image has no such file, so it reads no .env
REPO_MARKER = ".env.example"


class Settings(BaseSettings):
    """Every backend setting, validated once at startup.

    Values come from the environment only. get_settings() adds the repo's .env file.
    """

    model_config = SettingsConfigDict(env_ignore_empty=True, extra="ignore")

    # Required, so a missing ENV can never fall back to a debug-friendly default
    env: Literal["local", "test", "prod"]
    app_base_url: AnyHttpUrl = AnyHttpUrl("http://localhost:3000")
    ingest_base_url: AnyHttpUrl = AnyHttpUrl("http://localhost:8001")

    # DSNs carry passwords: repr, str and model_dump() show '**********'
    database_url: Secret[PostgresDsn]
    redis_url: Secret[RedisDsn]
    clickhouse_url: Secret[AnyHttpUrl]

    session_ttl_days: int = Field(default=30, ge=1)
    session_cookie_secure: bool = True
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

    @model_validator(mode="after")
    def _no_crash_flag_in_prod(self) -> Self:
        if self.env == "prod" and self.writer_test_crash_after_insert:
            raise ValueError("writer_test_crash_after_insert must be false when ENV=prod")
        return self


def find_env_file(start: Path) -> Path | None:
    """The .env beside the nearest ancestor of start (or start) that holds .env.example."""
    for directory in (start, *start.parents):
        if (directory / REPO_MARKER).is_file():
            return directory / ".env"
    return None


@lru_cache
def get_settings() -> Settings:
    """Build Settings once per process, from the environment and the repo's .env."""
    # Env vars still win over the file; a missing .env is ignored
    return Settings(_env_file=find_env_file(Path.cwd()))
