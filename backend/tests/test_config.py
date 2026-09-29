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
