from app.config import Settings


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
