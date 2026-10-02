import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import cron


def test_cron_rejects_request_when_secret_is_missing(monkeypatch):
    monkeypatch.setattr(cron.settings, "CRON_SECRET", "")

    with pytest.raises(HTTPException) as exc_info:
        cron.collect_due_prices(authorization=None, db=None)

    assert exc_info.value.status_code == 503


def test_cron_rejects_invalid_bearer_token(monkeypatch):
    monkeypatch.setattr(cron.settings, "CRON_SECRET", "expected")

    with pytest.raises(HTTPException) as exc_info:
        cron.collect_due_prices(authorization="Bearer wrong", db=None)

    assert exc_info.value.status_code == 401


def test_cron_runs_bounded_batch_with_valid_token(monkeypatch):
    expected = {"processed": 1, "has_more": False}
    monkeypatch.setattr(cron.settings, "CRON_SECRET", "expected")
    monkeypatch.setattr(cron.settings, "CRON_BATCH_SIZE", 250)
    monkeypatch.setattr(
        cron,
        "incremental_due_batch_update",
        lambda db, batch_size: expected if db == "db" and batch_size == 100 else None,
    )

    result = cron.collect_due_prices(authorization="Bearer expected", db="db")

    assert result == expected

def test_news_cron_runs_pipeline_with_valid_token(monkeypatch):
    expected = {"validation": {"status": "PASSED"}}
    monkeypatch.setattr(cron.settings, "CRON_SECRET", "expected")
    monkeypatch.setattr(
        cron,
        "run_news_pipeline",
        lambda db: expected if db == "db" else None,
    )

    result = cron.collect_and_link_news(
        authorization="Bearer expected", db="db",
    )

    assert result == expected


def test_news_cron_rejects_invalid_token(monkeypatch):
    monkeypatch.setattr(cron.settings, "CRON_SECRET", "expected")

    with pytest.raises(HTTPException) as exc_info:
        cron.collect_and_link_news(authorization="Bearer wrong", db=None)

    assert exc_info.value.status_code == 401
