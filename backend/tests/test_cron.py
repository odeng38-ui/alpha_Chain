from types import SimpleNamespace

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
    backtest = {"run_id": 12, "status": "INSUFFICIENT_SAMPLE", "created": True}
    monkeypatch.setattr(cron.settings, "CRON_SECRET", "expected")
    monkeypatch.setattr(cron.settings, "CRON_BATCH_SIZE", 250)
    monkeypatch.setattr(cron, "_record_cron_success", lambda db, job_name: None)
    monkeypatch.setattr(
        cron,
        "incremental_due_batch_update",
        lambda db, batch_size: expected.copy() if db == "db" and batch_size == 100 else None,
    )
    monkeypatch.setattr(
        cron,
        "run_daily_news_backtest",
        lambda db: backtest if db == "db" else None,
    )

    result = cron.collect_due_prices(authorization="Bearer expected", db="db")

    assert result == {**expected, "news_backtest": backtest}

def test_news_cron_runs_pipeline_with_valid_token(monkeypatch):
    expected = {"validation": {"status": "PASSED"}}
    monkeypatch.setattr(cron.settings, "CRON_SECRET", "expected")
    monkeypatch.setattr(cron, "_record_cron_success", lambda db, job_name: None)
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

def test_daily_news_backtest_reuses_same_day_run(monkeypatch):
    existing = SimpleNamespace(id=12, status="INSUFFICIENT_SAMPLE")

    class Query:
        def filter(self, *args):
            return self

        def order_by(self, *args):
            return self

        def first(self):
            return existing

    class Db:
        def query(self, *args):
            return Query()

    result = cron.run_daily_news_backtest(Db())

    assert result == {
        "run_id": 12,
        "status": "INSUFFICIENT_SAMPLE",
        "created": False,
    }

def test_us_market_pipeline_generates_five_day_candidates_for_touched_events(monkeypatch):
    sync_result = {"event_ids": [7, 8], "created": 2, "updated": 0, "failures": []}
    monkeypatch.setattr(
        cron,
        "USMarketEventService",
        lambda adapter: SimpleNamespace(sync=lambda db, lookback_days: sync_result),
    )
    monkeypatch.setattr(cron, "YahooUSMarketAdapter", lambda: "adapter")
    monkeypatch.setattr(
        cron,
        "EventImpactV4Service",
        lambda horizon: SimpleNamespace(
            generate=lambda db, event_id, limit: {
                "event_id": event_id, "horizon": horizon, "limit": limit,
            },
        ),
    )

    result = cron.run_us_market_pipeline("db")

    assert result["sync"] == sync_result
    assert result["generated"] == [
        {"event_id": 7, "horizon": 5, "limit": 20},
        {"event_id": 8, "horizon": 5, "limit": 20},
    ]
    assert result["failures"] == []


def test_weekly_event_backtest_reuses_same_iso_week_run():
    existing = SimpleNamespace(id=15, status="PARTIAL_ACCEPTANCE")

    class Query:
        def filter(self, *args):
            return self

        def order_by(self, *args):
            return self

        def first(self):
            return existing

    class Db:
        def query(self, *args):
            return Query()

    result = cron.run_weekly_event_impact_backtest(Db())

    assert result == {"run_id": 15, "status": "PARTIAL_ACCEPTANCE", "created": False}
