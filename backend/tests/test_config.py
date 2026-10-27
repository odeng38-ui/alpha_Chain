import pytest
from pydantic import ValidationError

from app.config import D2_PREVIEW_ENDPOINT_ID, Settings

D2_POOLED_URL = (
    f"postgresql://preview:secret@{D2_PREVIEW_ENDPOINT_ID}-pooler.neon.tech/app"
)
D2_DIRECT_URL = f"postgresql://preview:secret@{D2_PREVIEW_ENDPOINT_ID}.neon.tech/app"
INTEGRATION_URL = "postgresql://production:secret@ep-production-pooler.neon.tech/app"


def test_generic_postgres_url_selects_installed_psycopg2_driver():
    settings = Settings(
        DATABASE_URL="postgresql://user:password@database:5432/app",
    )

    assert settings.DATABASE_URL == (
        "postgresql+psycopg2://user:password@database:5432/app"
    )


def test_sqlite_url_is_unchanged():
    settings = Settings(DATABASE_URL="sqlite:///./test.db")

    assert settings.DATABASE_URL == "sqlite:///./test.db"


def test_scheduler_is_disabled_on_vercel(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("SCHEDULER_ENABLED", "true")

    settings = Settings()

    assert settings.SCHEDULER_ENABLED is False


def test_scheduler_can_be_disabled_explicitly(monkeypatch):
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")

    settings = Settings()

    assert settings.SCHEDULER_ENABLED is False

def test_price_initial_lookback_defaults_to_thirty_days(monkeypatch):
    monkeypatch.delenv("PRICE_INITIAL_LOOKBACK_DAYS", raising=False)

    settings = Settings()

    assert settings.PRICE_INITIAL_LOOKBACK_DAYS == 30


def test_price_initial_lookback_accepts_environment_override(monkeypatch):
    monkeypatch.setenv("PRICE_INITIAL_LOOKBACK_DAYS", "14")

    settings = Settings()

    assert settings.PRICE_INITIAL_LOOKBACK_DAYS == 14

def test_price_collection_defaults_are_serverless_safe(monkeypatch):
    monkeypatch.delenv("CRON_BATCH_SIZE", raising=False)
    monkeypatch.delenv("PRICE_FAILURE_RETRY_DAYS", raising=False)

    settings = Settings()

    assert settings.CRON_BATCH_SIZE == 100
    assert settings.PRICE_FAILURE_RETRY_DAYS == 7


def test_direct_postgres_url_selects_installed_psycopg2_driver():
    settings = Settings(
        DATABASE_URL="sqlite:///./test.db",
        DATABASE_DIRECT_URL="postgresql://user:password@direct-database:5432/app",
    )

    assert settings.DATABASE_DIRECT_URL == (
        "postgresql+psycopg2://user:password@direct-database:5432/app"
    )


def test_direct_database_url_defaults_to_empty(monkeypatch):
    monkeypatch.delenv("DATABASE_DIRECT_URL", raising=False)

    settings = Settings()

    assert settings.DATABASE_DIRECT_URL == ""


def test_production_keeps_existing_database_urls():
    settings = Settings(
        APP_ENV="production",
        SECRET_KEY="production-secret",
        ADMIN_API_KEY="admin-secret",
        VERCEL_ENV="production",
        VERCEL_GIT_COMMIT_REF="main",
        DATABASE_URL=INTEGRATION_URL,
        DATABASE_DIRECT_URL="postgresql://production:secret@ep-production.neon.tech/app",
        D2_PREVIEW_DATABASE_URL=D2_POOLED_URL,
        D2_PREVIEW_DATABASE_DIRECT_URL=D2_DIRECT_URL,
    )

    assert settings.effective_database_url == settings.DATABASE_URL
    assert settings.effective_database_direct_url == settings.DATABASE_DIRECT_URL


def test_d2_preview_selects_dedicated_database_urls():
    settings = Settings(
        VERCEL_ENV="preview",
        VERCEL_GIT_COMMIT_REF="phase-d2-daily-prices",
        DATABASE_URL=D2_POOLED_URL,
        D2_PREVIEW_DATABASE_URL=D2_POOLED_URL,
        D2_PREVIEW_DATABASE_DIRECT_URL=D2_DIRECT_URL,
    )

    assert settings.effective_database_url == (
        f"postgresql+psycopg2://preview:secret@{D2_PREVIEW_ENDPOINT_ID}-pooler.neon.tech/app"
    )
    assert settings.effective_database_direct_url == (
        f"postgresql+psycopg2://preview:secret@{D2_PREVIEW_ENDPOINT_ID}.neon.tech/app"
    )


def test_d2_preview_accepts_same_endpoint_pooled_and_direct_urls():
    settings = Settings(
        VERCEL_ENV="preview",
        VERCEL_GIT_COMMIT_REF="phase-d2-daily-prices",
        DATABASE_URL=D2_POOLED_URL,
        D2_PREVIEW_DATABASE_URL=(
            f"postgresql://another-user:another-secret@"
            f"{D2_PREVIEW_ENDPOINT_ID}-pooler.us-east-2.aws.neon.tech:5432/app"
        ),
        D2_PREVIEW_DATABASE_DIRECT_URL=(
            f"postgresql://direct:direct-secret@"
            f"{D2_PREVIEW_ENDPOINT_ID}.us-east-2.aws.neon.tech/app"
        ),
    )

    assert settings.effective_database_url == settings.D2_PREVIEW_DATABASE_URL


@pytest.mark.parametrize(
    ("direct_url", "message"),
    [
        (
            f"postgresql://preview:secret@{D2_PREVIEW_ENDPOINT_ID}.neon.tech/other",
            "same endpoint, database, and port",
        ),
        (
            f"postgresql://preview:secret@{D2_PREVIEW_ENDPOINT_ID}.neon.tech:5433/app",
            "same endpoint, database, and port",
        ),
        (
            "postgresql://preview:secret@ep-another.neon.tech/app",
            "approved endpoint",
        ),
    ],
)
def test_d2_preview_rejects_mismatched_target(direct_url, message):
    with pytest.raises(ValidationError, match=message):
        Settings(
            VERCEL_ENV="preview",
            VERCEL_GIT_COMMIT_REF="phase-d2-daily-prices",
            DATABASE_URL=D2_POOLED_URL,
            D2_PREVIEW_DATABASE_URL=D2_POOLED_URL,
            D2_PREVIEW_DATABASE_DIRECT_URL=direct_url,
        )


def test_d2_preview_rejects_unapproved_integration_endpoint():
    with pytest.raises(ValidationError, match="approved endpoint"):
        Settings(
            VERCEL_ENV="preview",
            VERCEL_GIT_COMMIT_REF="phase-d2-daily-prices",
            DATABASE_URL=INTEGRATION_URL,
            D2_PREVIEW_DATABASE_URL=D2_POOLED_URL,
            D2_PREVIEW_DATABASE_DIRECT_URL=D2_DIRECT_URL,
        )


@pytest.mark.parametrize(
    ("pooled_url", "direct_url", "message"),
    [
        ("", D2_DIRECT_URL, "pooled database URL is required"),
        (D2_POOLED_URL, "", "direct database URL is required"),
        ("sqlite:///preview.db", D2_DIRECT_URL, "pooled database URL must be PostgreSQL"),
        (D2_POOLED_URL, "not-a-url", "direct database URL must be PostgreSQL"),
        (
            D2_POOLED_URL,
            "postgresql://preview:secret@ep-preview-pooler.neon.tech/app",
            "direct database URL cannot use a pooler",
        ),
        (D2_POOLED_URL, "postgresql://preview:secret@ep-unapproved.neon.tech/app", "approved endpoint"),
    ],
)
def test_d2_preview_rejects_unsafe_database_urls(pooled_url, direct_url, message):
    with pytest.raises(ValidationError, match=message):
        Settings(
            VERCEL_ENV="preview",
            VERCEL_GIT_COMMIT_REF="phase-d2-daily-prices",
            DATABASE_URL=D2_POOLED_URL,
            D2_PREVIEW_DATABASE_URL=pooled_url,
            D2_PREVIEW_DATABASE_DIRECT_URL=direct_url,
        )


@pytest.mark.parametrize("git_ref", ["another-preview", ""])
def test_other_or_unidentified_preview_rejects_d2_urls(git_ref):
    with pytest.raises(ValidationError, match="approved Preview Git branch"):
        Settings(
            VERCEL_ENV="preview",
            VERCEL_GIT_COMMIT_REF=git_ref,
            DATABASE_URL=INTEGRATION_URL,
            D2_PREVIEW_DATABASE_URL=D2_POOLED_URL,
            D2_PREVIEW_DATABASE_DIRECT_URL=D2_DIRECT_URL,
        )


def test_other_preview_without_d2_urls_keeps_existing_database_url():
    settings = Settings(
        VERCEL_ENV="preview",
        VERCEL_GIT_COMMIT_REF="another-preview",
        DATABASE_URL=INTEGRATION_URL,
    )

    assert settings.effective_database_url == settings.DATABASE_URL


def test_d2_preview_validation_does_not_expose_secrets():
    pooled_secret = "pooled-password-must-not-leak"
    direct_secret = "direct-password-must-not-leak"

    with pytest.raises(ValidationError) as exc_info:
        Settings(
            VERCEL_ENV="preview",
            VERCEL_GIT_COMMIT_REF="phase-d2-daily-prices",
            DATABASE_URL=INTEGRATION_URL,
            D2_PREVIEW_DATABASE_URL=(
                f"mysql://preview:{pooled_secret}@preview.example/app"
            ),
            D2_PREVIEW_DATABASE_DIRECT_URL=(
                f"postgresql://preview:{direct_secret}@preview.example/app"
            ),
        )

    error = str(exc_info.value)
    assert pooled_secret not in error
    assert direct_secret not in error
