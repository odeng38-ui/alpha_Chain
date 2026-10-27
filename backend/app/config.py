import os
from pathlib import Path

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_ENV = os.getenv("APP_ENV", "development")
D2_PREVIEW_GIT_REF = "phase-d2-daily-prices"


class Settings(BaseSettings):
    APP_ENV: str = os.getenv("APP_ENV", "development")
    PROJECT_NAME: str = "Alpha Chain API"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"
    
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./alphachain_dev.db")
    DATABASE_DIRECT_URL: str = os.getenv("DATABASE_DIRECT_URL", "")
    D2_PREVIEW_DATABASE_URL: str = os.getenv("D2_PREVIEW_DATABASE_URL", "")
    D2_PREVIEW_DATABASE_DIRECT_URL: str = os.getenv(
        "D2_PREVIEW_DATABASE_DIRECT_URL", ""
    )
    VERCEL_ENV: str = os.getenv("VERCEL_ENV", "")
    VERCEL_GIT_COMMIT_REF: str = os.getenv("VERCEL_GIT_COMMIT_REF", "")
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    
    DART_API_KEY: str = os.getenv("DART_API_KEY", "")
    FRED_API_KEY: str = os.getenv("FRED_API_KEY", "")
    STOCK_DATA_API_KEY: str = os.getenv("STOCK_DATA_API_KEY", "")
    KRX_ID: str = os.getenv("KRX_ID", "")
    KRX_PW: str = os.getenv("KRX_PW", "")
    
    SECRET_KEY: str = os.getenv("SECRET_KEY", "dev_secret")
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    DART_RAW_DIR: str = os.getenv("DART_RAW_DIR", "./data/dart")
    ADMIN_API_KEY: str = os.getenv("ADMIN_API_KEY", "")
    CRON_SECRET: str = os.getenv("CRON_SECRET", "")
    CRON_BATCH_SIZE: int = int(os.getenv("CRON_BATCH_SIZE", "100"))
    DART_CRON_BATCH_SIZE: int = int(os.getenv("DART_CRON_BATCH_SIZE", "2"))
    PRICE_INITIAL_LOOKBACK_DAYS: int = int(os.getenv("PRICE_INITIAL_LOOKBACK_DAYS", "30"))
    PRICE_FAILURE_RETRY_DAYS: int = int(os.getenv("PRICE_FAILURE_RETRY_DAYS", "7"))
    CORS_ORIGINS: str = os.getenv("CORS_ORIGINS", "http://localhost:3000")
    DOCS_ENABLED: bool = os.getenv("DOCS_ENABLED", "true").lower() == "true"
    SCHEDULER_ENABLED: bool = os.getenv("SCHEDULER_ENABLED", "true").lower() == "true"
    
    model_config = SettingsConfigDict(
        case_sensitive=True,
        env_file=(PROJECT_ROOT / ".env", PROJECT_ROOT / f".env.{APP_ENV}"),
        extra="ignore",
        hide_input_in_errors=True,
    )

    @field_validator(
        "DATABASE_URL",
        "DATABASE_DIRECT_URL",
        "D2_PREVIEW_DATABASE_URL",
        "D2_PREVIEW_DATABASE_DIRECT_URL",
    )
    @classmethod
    def select_installed_postgres_driver(cls, value: str) -> str:
        """Keep generic PostgreSQL URLs on the installed psycopg2 driver."""
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+psycopg2://", 1)
        if value.startswith("postgres://"):
            return value.replace("postgres://", "postgresql+psycopg2://", 1)
        return value


    @model_validator(mode="after")
    def validate_production_secrets(self):
        if os.getenv("VERCEL"):
            self.SCHEDULER_ENABLED = False
        if self.APP_ENV == "production":
            if self.SECRET_KEY in {"", "dev_secret"}:
                raise ValueError("SECRET_KEY must be set in production")
            if not self.ADMIN_API_KEY:
                raise ValueError("ADMIN_API_KEY must be set in production")
        self._validate_d2_preview_database_urls()
        return self

    def _validate_d2_preview_database_urls(self) -> None:
        is_preview = self.VERCEL_ENV == "preview"
        is_d2_branch = self.VERCEL_GIT_COMMIT_REF == D2_PREVIEW_GIT_REF
        has_d2_urls = bool(
            self.D2_PREVIEW_DATABASE_URL or self.D2_PREVIEW_DATABASE_DIRECT_URL
        )

        if is_preview and is_d2_branch:
            if not self.D2_PREVIEW_DATABASE_URL:
                raise ValueError("D-2 Preview pooled database URL is required")
            if not self.D2_PREVIEW_DATABASE_DIRECT_URL:
                raise ValueError("D-2 Preview direct database URL is required")
            pooled_url = self._validated_postgres_url(
                self.D2_PREVIEW_DATABASE_URL,
                "D-2 Preview pooled database URL must be PostgreSQL",
            )
            direct_url = self._validated_postgres_url(
                self.D2_PREVIEW_DATABASE_DIRECT_URL,
                "D-2 Preview direct database URL must be PostgreSQL",
            )
            integration_url = self._validated_postgres_url(
                self.DATABASE_URL,
                "DATABASE_URL must be PostgreSQL for D-2 Preview",
            )
            if "pooler" in (direct_url.host or "").lower():
                raise ValueError("D-2 Preview direct database URL cannot use a pooler")
            if pooled_url == integration_url:
                raise ValueError(
                    "D-2 Preview pooled database URL must differ from DATABASE_URL"
                )
            return

        if is_preview and has_d2_urls:
            raise ValueError(
                "D-2 Preview database URLs require the approved Preview Git branch"
            )

    @staticmethod
    def _validated_postgres_url(value: str, message: str):
        try:
            url = make_url(value)
        except ArgumentError as exc:
            raise ValueError(message) from exc
        if url.get_backend_name() != "postgresql":
            raise ValueError(message)
        return url

    @property
    def effective_database_url(self) -> str:
        if (
            self.VERCEL_ENV == "preview"
            and self.VERCEL_GIT_COMMIT_REF == D2_PREVIEW_GIT_REF
        ):
            return self.D2_PREVIEW_DATABASE_URL
        return self.DATABASE_URL

    @property
    def effective_database_direct_url(self) -> str:
        if (
            self.VERCEL_ENV == "preview"
            and self.VERCEL_GIT_COMMIT_REF == D2_PREVIEW_GIT_REF
        ):
            return self.D2_PREVIEW_DATABASE_DIRECT_URL
        return self.DATABASE_DIRECT_URL

    @property
    def cors_origins(self) -> list[str]:
        """Return the explicit browser origins allowed to call the API."""
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

settings = Settings()
