from pathlib import Path


def test_alembic_uses_validated_effective_database_url():
    source = (
        Path(__file__).resolve().parents[1] / "alembic" / "env.py"
    ).read_text(encoding="utf-8")

    assert source.count("settings.effective_database_url") == 2
    assert "settings.DATABASE_URL" not in source
