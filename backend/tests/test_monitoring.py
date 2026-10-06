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
    IdentifierMap,
    MacroObservation,
    Security,
)
from app.services.master_lifecycle_service import (
    apply_master_lifecycle_snapshot,
    build_master_lifecycle_report,
    compare_master_to_kind_snapshot,
    normalize_kind_listings,
    parse_kind_listings,
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


def test_operational_alerts_ignore_closed_security_price_failures(db):
    company = Company(name="Closed Corp")
    db.add(company)
    db.flush()
    security = Security(
        company_id=company.id,
        market="KOSPI",
        ticker="000099",
        effective_to=date(2026, 10, 5),
    )
    db.add(security)
    db.flush()
    db.add(CollectionCheckpoint(
        job_name="daily_price",
        security_id=security.id,
        status="FAILED",
        last_error="No price data found for ticker=000099",
    ))
    db.commit()

    result = build_operational_alerts(
        db,
        now=datetime(2026, 10, 6, tzinfo=timezone.utc),
    )

    assert result["summary"]["failed_jobs"] == 0
    assert result["summary"]["action_required_failures"] == 0
    assert result["price_recovery"]["total_checkpoints"] == 0
    assert result["price_recovery"]["status"] == "healthy"


def test_master_lifecycle_report_is_read_only_and_classifies_candidates(db):
    companies = [
        Company(name="Priced Corp"),
        Company(name="Unavailable Corp"),
        Company(name="Closed Corp", status="DELISTED"),
        Company(name="Unattempted Corp"),
    ]
    db.add_all(companies)
    db.flush()
    securities = [
        Security(company_id=companies[0].id, market="KOSPI", ticker="0000A1"),
        Security(company_id=companies[1].id, market="KOSDAQ", ticker="000002"),
        Security(company_id=companies[2].id, market="KOSPI", ticker="000003"),
        Security(company_id=companies[3].id, market="KOSDAQ", ticker="000004"),
    ]
    db.add_all(securities)
    db.flush()
    db.add(DailyPrice(
        security_id=securities[0].id,
        trade_date=date(2026, 10, 2),
        close=100,
    ))
    db.add_all([
        CollectionCheckpoint(
            job_name="daily_price",
            security_id=securities[1].id,
            status="FAILED",
            last_error="No price data found for ticker=000002",
        ),
        CollectionCheckpoint(
            job_name="daily_price",
            security_id=securities[2].id,
            status="SUCCESS",
            last_success_date=date(2026, 10, 2),
        ),
    ])
    db.commit()

    report = build_master_lifecycle_report(db, sample_limit=1)

    assert report["summary"] == {
        "active_common": 4,
        "priced": 1,
        "unresolved": 3,
        "coverage_percent": 25.0,
        "safe_to_auto_close": 0,
    }
    assert report["categories"]["priced"] == 1
    assert report["categories"]["unavailable_candidate"] == 1
    assert report["categories"]["status_mismatch"] == 1
    assert report["categories"]["unattempted"] == 1
    assert report["policy"]["auto_close_enabled"] is False
    assert all(security.effective_to is None for security in securities)

def test_parse_kind_listings_reads_official_excel_html():
    html = """
    <table>
      <tr><th>회사명</th><th>시장구분</th><th>종목코드</th><th>업종</th><th>제품</th><th>상장일</th></tr>
      <tr><td>Alpha</td><td>유가</td><td>000001</td><td>A</td><td>P</td><td>2020-01-01</td></tr>
      <tr><td>Beta</td><td>코스닥</td><td>0000A2</td><td>B</td><td>Q</td><td>2021-01-01</td></tr>
      <tr><td>Konex</td><td>코넥스</td><td>000003</td><td>C</td><td>R</td><td>2022-01-01</td></tr>
    </table>
    """.encode("euc-kr")

    assert parse_kind_listings(html) == [
        {"name": "Alpha", "market": "KOSPI", "ticker": "000001", "listed_at": "2020-01-01"},
        {"name": "Beta", "market": "KOSDAQ", "ticker": "0000A2", "listed_at": "2021-01-01"},
    ]


def test_kind_snapshot_comparison_is_preview_only(db):
    companies = [Company(name="Listed"), Company(name="Legacy")]
    db.add_all(companies)
    db.flush()
    listed = Security(
        company_id=companies[0].id,
        market="UNKNOWN",
        ticker="000001",
        security_type="COMMON",
    )
    legacy = Security(
        company_id=companies[1].id,
        market="UNKNOWN",
        ticker="000002",
        security_type="COMMON",
    )
    db.add_all([listed, legacy])
    db.flush()
    db.add(CollectionCheckpoint(
        job_name="daily_price",
        security_id=legacy.id,
        status="FAILED",
        last_error="No price data found for ticker=000002",
    ))
    db.commit()

    report = compare_master_to_kind_snapshot(
        db,
        [
            {"name": "Listed", "market": "KOSPI", "ticker": "000001", "listed_at": "2020-01-01"},
            {"name": "New", "market": "KOSDAQ", "ticker": "000003", "listed_at": "2026-01-01"},
        ],
        as_of=date(2026, 10, 5),
    )

    assert report["comparison"] == {
        "active_common": 2,
        "confirmed_listed": 1,
        "absent_from_snapshot": 1,
        "safe_close_candidates": 1,
        "new_snapshot_tickers": 1,
        "market_mismatches": 0,
    }
    assert report["policy"]["mutations_applied"] is False
    assert legacy.effective_to is None

def test_kind_snapshot_normalization_deduplicates_only_identical_rows():
    row = {"name": "Alpha", "market": "KOSPI", "ticker": "000001", "listed_at": "2020-01-01"}
    filler = [
        {"name": f"Corp {index}", "market": "KOSDAQ", "ticker": f"{index:06d}", "listed_at": "2020-01-01"}
        for index in range(2, 1002)
    ]

    normalized = normalize_kind_listings([row, row.copy(), *filler])

    assert len(normalized) == 1001
    conflicting = {**row, "market": "KOSDAQ"}
    with pytest.raises(ValueError, match="conflicting KIND rows"):
        normalize_kind_listings([row, conflicting, *filler])

def test_apply_master_lifecycle_snapshot_is_guarded_and_reversible(db):
    listed_company = Company(name="Listed")
    legacy_company = Company(name="Legacy")
    db.add_all([listed_company, legacy_company])
    db.flush()
    listed = Security(
        company_id=listed_company.id,
        market="UNKNOWN",
        ticker="000001",
        security_type="COMMON",
    )
    legacy = Security(
        company_id=legacy_company.id,
        market="UNKNOWN",
        ticker="000002",
        security_type="COMMON",
    )
    db.add_all([listed, legacy])
    db.flush()
    db.add(IdentifierMap(
        company_id=legacy_company.id,
        security_id=legacy.id,
        source="KRX_TICKER",
        source_id_value="000002",
    ))
    db.add(CollectionCheckpoint(
        job_name="daily_price",
        security_id=legacy.id,
        status="FAILED",
        last_error="No price data found for ticker=000002",
    ))
    db.commit()
    listings = [
        {"name": "Listed", "market": "KOSPI", "ticker": "000001", "listed_at": "2020-01-01"},
    ]
    preview = compare_master_to_kind_snapshot(
        db,
        listings,
        as_of=date(2026, 10, 5),
    )

    result = apply_master_lifecycle_snapshot(
        db,
        listings,
        expected_sha256=preview["snapshot"]["sha256"],
        expected_safe_close_candidates=1,
        expected_confirmed_listed=1,
        as_of=date(2026, 10, 5),
    )

    assert result["applied"] == {
        "securities_closed": 1,
        "markets_updated": 1,
        "companies_closed": 1,
        "remaining_safe_close_candidates": 0,
        "remaining_market_updates": 0,
    }
    assert listed.market == "KOSPI"
    assert legacy.effective_to == date(2026, 10, 5)
    assert legacy.delisted_at == date(2026, 10, 5)
    assert legacy.identifier_maps[0].effective_to == date(2026, 10, 5)
    assert legacy_company.status == "DELISTED"
    audit = db.query(AuditLog).filter(
        AuditLog.action == "apply_master_lifecycle",
    ).one()
    assert audit.after_state["securities_closed"] == 1


def test_apply_master_lifecycle_snapshot_batches_market_updates(db):
    companies = [Company(name="Listed One"), Company(name="Listed Two")]
    db.add_all(companies)
    db.flush()
    securities = [
        Security(
            company_id=company.id,
            market="UNKNOWN",
            ticker=f"00000{index}",
            security_type="COMMON",
        )
        for index, company in enumerate(companies, start=1)
    ]
    db.add_all(securities)
    db.commit()
    listings = [
        {
            "name": company.name,
            "market": "KOSPI",
            "ticker": security.ticker,
            "listed_at": "2020-01-01",
        }
        for company, security in zip(companies, securities, strict=True)
    ]
    preview = compare_master_to_kind_snapshot(db, listings)

    result = apply_master_lifecycle_snapshot(
        db,
        listings,
        expected_sha256=preview["snapshot"]["sha256"],
        expected_safe_close_candidates=0,
        expected_confirmed_listed=2,
        max_market_update=1,
    )

    assert result["applied"]["markets_updated"] == 1
    assert result["applied"]["remaining_market_updates"] == 1
    assert [security.market for security in securities] == ["KOSPI", "UNKNOWN"]


def test_apply_master_lifecycle_snapshot_rejects_changed_preview(db):
    company = Company(name="Legacy")
    db.add(company)
    db.flush()
    security = Security(
        company_id=company.id,
        market="UNKNOWN",
        ticker="000002",
        security_type="COMMON",
    )
    db.add(security)
    db.flush()
    db.add(CollectionCheckpoint(
        job_name="daily_price",
        security_id=security.id,
        status="FAILED",
        last_error="No price data found for ticker=000002",
    ))
    db.commit()

    with pytest.raises(ValueError, match="snapshot hash"):
        apply_master_lifecycle_snapshot(
            db,
            [],
            expected_sha256="wrong",
            expected_safe_close_candidates=1,
            expected_confirmed_listed=0,
            as_of=date(2026, 10, 5),
        )

    assert security.effective_to is None