from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.metrics import RequestMetrics
from app.models.schema import (
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
    assert result["summary"] == {"failed_jobs": 0, "stale_or_missing_sources": 3}
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
    assert result["summary"] == {"failed_jobs": 2, "stale_or_missing_sources": 1}
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
