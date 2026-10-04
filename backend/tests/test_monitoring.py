from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1.endpoints.ui import operations_health
from app.db.session import Base
from app.metrics import RequestMetrics
from app.models.schema import (
    AuditLog,
    BacktestRun,
    CollectionCheckpoint,
    Company,
    DailyPrice,
    DartSyncState,
    Filing,
    MacroObservation,
    Security,
)
from app.services.monitoring import build_operational_alerts

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
SessionLocal = sessionmaker(bind=engine)


@pytest.fixture
def db():
    Base.metadata.create_all(engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)


def test_request_metrics_render_prometheus_counters():
    metrics = RequestMetrics()
    metrics.observe("get", "/companies/{company_id}", 200, 0.125)
    metrics.observe("GET", "/companies/{company_id}", 200, 0.375)

    rendered = metrics.render()

    assert 'method="GET",path="/companies/{company_id}",status="200"} 2' in rendered
    assert 'method="GET",path="/companies/{company_id}"} 0.500000' in rendered


def test_operational_alerts_warn_when_sources_are_missing(db):
    result = build_operational_alerts(
        db,
        now=datetime(2026, 9, 28, tzinfo=timezone.utc),
    )

    assert result["status"] == "warning"
    assert result["summary"] == {
        "failed_jobs": 0,
        "action_required_failures": 0,
        "stale_or_missing_sources": 3,
        "automation_issues": 3,
        "model_degraded": False,
    }
    assert {row["status"] for row in result["data_freshness"]} == {"missing"}


def test_operational_alerts_report_failures_and_freshness(db):
    now = datetime(2026, 9, 28, 12, tzinfo=timezone.utc)
    company = Company(name="Monitoring Corp")
    db.add(company)
    db.flush()
    security = Security(company_id=company.id, market="KOSPI", ticker="000001")
    db.add(security)
    db.flush()
    db.add_all([
        DailyPrice(security_id=security.id, trade_date=date(2026, 9, 27), close=100),
        Filing(
            rcept_no="20260928000001",
            company_id=company.id,
            title="Current filing",
            filed_at=datetime(2026, 9, 27),
            available_at=datetime(2026, 9, 27),
        ),
        MacroObservation(
            series_id="TEST",
            observation_date=date(2026, 9, 1),
            vintage_date=date(2026, 9, 1),
            available_at=datetime(2026, 7, 1),
            value=1,
        ),
        CollectionCheckpoint(
            job_name="daily_prices",
            security_id=security.id,
            status="FAILED",
            last_error="provider timeout",
        ),
        DartSyncState(company_id=company.id, status="error", last_error="rate limited"),
    ])
    db.commit()

    result = build_operational_alerts(db, now=now)

    assert result["status"] == "critical"
    assert result["summary"] == {
        "failed_jobs": 2,
        "action_required_failures": 2,
        "stale_or_missing_sources": 1,
        "automation_issues": 3,
        "model_degraded": False,
    }
    statuses = {row["source"]: row["status"] for row in result["data_freshness"]}
    assert statuses == {
        "daily_prices": "ok",
        "filings": "ok",
        "macro_observations": "stale",
    }
    assert {row["last_error"] for row in result["job_failures"]} == {
        "provider timeout",
        "rate limited",
    }

def test_operational_alerts_report_cron_heartbeats_and_model_approval(db):
    now = datetime(2026, 10, 4, 12, tzinfo=timezone.utc)
    for job_name in ("news", "prices"):
        db.add(AuditLog(
            actor="vercel-cron", action="completed", resource_type="cron_run",
            resource_id=job_name, before_state=None, after_state={"status": "SUCCESS"},
            request_id="test", created_at=datetime(2026, 10, 4, 9),
        ))
    db.add(BacktestRun(
        name="impact-v4-weekly-2026-W40", score_version="impact-v4",
        horizon="1d,5d", config={}, dataset_hash="f" * 64,
        parameter_adjustments=3, status="PARTIAL_ACCEPTANCE",
        completed_at=datetime(2026, 10, 3, 9),
        report={
            "model_metadata": {"calibration_version": "impact-v4-calibration-1"},
            "temporal_validation": {"holdout": {
                "events": 10,
                "horizon_acceptance": {"5d": {"status": "PASSED"}},
            }},
        },
    ))
    db.commit()

    result = build_operational_alerts(db, now=now)

    assert {item["status"] for item in result["automation"]} == {"ok"}
    assert result["summary"]["automation_issues"] == 0
    assert result["model_health"]["status"] == "approved"
    assert result["model_health"]["approved"] is True
    assert all(result["model_health"]["approval_checks"].values())

def test_public_operations_health_omits_failure_details(db):
    result = operations_health(db)

    assert "job_failures" not in result
    assert set(result) == {
        "status", "checked_at", "summary", "price_recovery", "data_freshness", "automation", "model_health",
    }


def test_price_recovery_distinguishes_retryable_and_action_required(db):
    now = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
    company = Company(name="Recovery Corp")
    db.add(company)
    db.flush()
    securities = [
        Security(company_id=company.id, market="KOSPI", ticker=f"00000{index}")
        for index in range(1, 6)
    ]
    db.add_all(securities)
    db.flush()
    db.add_all([
        CollectionCheckpoint(
            job_name="daily_price", security_id=securities[0].id,
            status="SUCCESS", last_success_date=date(2026, 10, 9),
        ),
        CollectionCheckpoint(
            job_name="daily_price", security_id=securities[1].id,
            status="FAILED", last_success_date=date(2026, 9, 30),
            last_error="No price data found for ticker=000002",
            updated_at=datetime(2026, 10, 1),
        ),
        CollectionCheckpoint(
            job_name="daily_price", security_id=securities[2].id,
            status="FAILED", last_success_date=date(2026, 10, 8),
            last_error="No price data found for ticker=000003",
            updated_at=datetime(2026, 10, 9),
        ),
        CollectionCheckpoint(
            job_name="daily_price", security_id=securities[3].id,
            status="FAILED", last_error="provider timeout",
            updated_at=datetime(2026, 10, 1),
        ),
        DailyPrice(
            security_id=securities[4].id,
            trade_date=date(2026, 9, 30),
            close=100,
        ),
        CollectionCheckpoint(
            job_name="daily_price", security_id=securities[4].id,
            status="FAILED",
            last_error="No price data found for ticker=000005",
            updated_at=datetime(2026, 10, 1),
        ),
    ])
    db.commit()

    recovery = build_operational_alerts(db, now=now)["price_recovery"]

    assert recovery == {
        "total_checkpoints": 5,
        "healthy": 1,
        "recoverable_failures": 3,
        "retry_eligible": 2,
        "retry_waiting": 1,
        "action_required": 1,
        "pending": 0,
        "progress_percent": 20.0,
        "retry_after_days": 7,
        "status": "action_required",
    }
