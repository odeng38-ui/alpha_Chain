import runpy
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from app.config import D2_PREVIEW_ENDPOINT_ID


def test_alembic_uses_validated_effective_database_url():
    source = (
        Path(__file__).resolve().parents[1] / "alembic" / "env.py"
    ).read_text(encoding="utf-8")

    assert source.count("settings.effective_database_url") == 2
    assert "settings.DATABASE_URL" not in source


def test_alembic_stops_before_engine_creation_when_preview_validation_fails(
    monkeypatch,
):
    engine_from_config = Mock()
    monkeypatch.setattr("sqlalchemy.engine_from_config", engine_from_config)
    monkeypatch.setenv("VERCEL_ENV", "preview")
    monkeypatch.setenv("VERCEL_GIT_COMMIT_REF", "phase-d2-daily-prices")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://preview:integration-secret@ep-unapproved.neon.tech/app",
    )
    monkeypatch.setenv(
        "D2_PREVIEW_DATABASE_URL",
        f"postgresql://preview:pooled-secret@{D2_PREVIEW_ENDPOINT_ID}-pooler.neon.tech/app",
    )
    monkeypatch.setenv(
        "D2_PREVIEW_DATABASE_DIRECT_URL",
        f"postgresql://preview:direct-secret@{D2_PREVIEW_ENDPOINT_ID}.neon.tech/app",
    )
    monkeypatch.delitem(sys.modules, "app.config", raising=False)

    with pytest.raises(ValidationError, match="approved endpoint"):
        runpy.run_path(
            str(Path(__file__).resolve().parents[1] / "alembic" / "env.py")
        )

    engine_from_config.assert_not_called()