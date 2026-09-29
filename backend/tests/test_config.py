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
