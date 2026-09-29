import os
from pathlib import Path

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_ENV = os.getenv("APP_ENV", "development")


class Settings(BaseSettings):
    APP_ENV: str = os.getenv("APP_ENV", "development")
    PROJECT_NAME: str = "Alpha Chain API"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"
    
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./alphachain_dev.db")
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
    CORS_ORIGINS: str = os.getenv("CORS_ORIGINS", "http://localhost:3000")
    DOCS_ENABLED: bool = os.getenv("DOCS_ENABLED", "true").lower() == "true"
    
    model_config = SettingsConfigDict(
        case_sensitive=True,
        env_file=(PROJECT_ROOT / ".env", PROJECT_ROOT / f".env.{APP_ENV}"),
        extra="ignore",
    )

    @field_validator("DATABASE_URL")
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
        if self.APP_ENV == "production":
            if self.SECRET_KEY in {"", "dev_secret"}:
                raise ValueError("SECRET_KEY must be set in production")
            if not self.ADMIN_API_KEY:
                raise ValueError("ADMIN_API_KEY must be set in production")
        return self

    @property
    def cors_origins(self) -> list[str]:
        """Return the explicit browser origins allowed to call the API."""
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

settings = Settings()
