from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1.endpoints import backtests
from app.db.session import Base
from app.models.schema import BacktestRun, Company, DailyPrice, GlobalEvent, Security
from app.services.event_impact_backtest_service import EventImpactBacktestService

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Session = sessionmaker(bind=engine)


@pytest.fixture(autouse=True)
def database():
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


def test_event_backtest_is_point_in_time_and_reproducible():
    db = Session()
    company = Company(name="Chip", corp_code="12345678", industry_id="SEMICONDUCTORS_ELECTRONICS")
    db.add(company)
    db.flush()
    security = Security(company_id=company.id, market="KOSPI", ticker="123456", security_type="COMMON")
    db.add(security)
    db.flush()
    start = date(2025, 1, 2)
    for index in range(14):
        value = Decimal(str(100 + index * 2))
        db.add(DailyPrice(security_id=security.id, trade_date=start + timedelta(days=index),
                          open=value - 1, close=value, adjusted_close=value))
    for index, available in enumerate((datetime(2025, 1, 3), datetime(2025, 1, 10)), 1):
        db.add(GlobalEvent(
            external_id=f"event-{index}", source="TEST", origin_country="US",
            event_kind="MARKET_SHOCK", symbol="^SOX", title=f"event {index}",
            direction="POSITIVE", occurred_at=available - timedelta(days=1),
            available_at=available, shock_score=70, event_metadata={}, raw_hash=str(index) * 64,
        ))
    db.commit()
    service = EventImpactBacktestService()
    first = service.run(db, "first", max_events=2, candidates_per_event=1, horizons=(1, 5))
    second = service.run(db, "second", max_events=2, candidates_per_event=1, horizons=(1, 5))
    assert first.dataset_hash == second.dataset_hash
    assert first.report["summary"] == {"events": 2, "candidate_rows": 2, "outcome_observations": 4}
    assert first.report["metrics"]["1d"]["direction_hit_rate"] == 1.0
    assert first.report["metrics"]["1d"]["misses"] == 0
    assert first.report["diagnostics"]["1d"]["dimensions"]["event_symbol"]["^SOX"][
        "observations"
    ] == 2
    assert first.report["diagnostics"]["1d"]["worst_segments"] == []
    assert first.status == "FAILED_ACCEPTANCE"
    assert first.report["horizon_acceptance"]["1d"]["status"] == "FAILED"
    assert "INSUFFICIENT_OUTCOMES_1D" in first.report["failure_conditions"]
    assert first.report["bias_checklist"]["pre_event_prices_only"] is True
    assert first.report["samples"][0]["available_at"] == "2025-01-03T00:00:00"
    assert db.query(BacktestRun).count() == 2
    db.close()

def test_static_event_backtest_route_precedes_dynamic_run_route():
    paths = [route.path for route in backtests.router.routes]
    assert paths.index("/backtests/event-impact") < paths.index("/backtests/{run_id}")
    assert paths.index("/backtests/event-impact/calibration-status") < paths.index(
        "/backtests/{run_id}"
    )

def test_horizon_acceptance_allows_five_day_model_only():
    metrics = {
        "1d": {"observations": 30, "direction_hit_rate": 0.33,
               "average_aligned_return": -0.006},
        "5d": {"observations": 29, "direction_hit_rate": 0.62,
               "average_aligned_return": 0.015},
    }
    acceptance, failures, status = EventImpactBacktestService._acceptance(metrics)
    assert acceptance["1d"]["status"] == "FAILED"
    assert acceptance["5d"]["status"] == "PASSED"
    assert status == "PARTIAL_ACCEPTANCE"
    assert "DIRECTION_HIT_RATE_BELOW_50_1D" in failures

def test_diagnostics_rank_worst_segments_by_miss_rate():
    samples = [
        {
            "event_symbol": "^SOX", "event_direction": "POSITIVE", "rank": 1,
            "confidence": 0.8, "historical_sample_count": 8,
            "outcomes": {"1d": {"raw_return": -0.02, "aligned_return": -0.02}},
        },
        {
            "event_symbol": "^SOX", "event_direction": "POSITIVE", "rank": 2,
            "confidence": 0.8, "historical_sample_count": 8,
            "outcomes": {"1d": {"raw_return": -0.01, "aligned_return": -0.01}},
        },
        {
            "event_symbol": "CL=F", "event_direction": "NEGATIVE", "rank": 8,
            "confidence": 0.55, "historical_sample_count": 3,
            "outcomes": {"1d": {"raw_return": -0.01, "aligned_return": 0.01}},
        },
        {
            "event_symbol": "CL=F", "event_direction": "NEGATIVE", "rank": 9,
            "confidence": 0.55, "historical_sample_count": 3,
            "outcomes": {"1d": {"raw_return": -0.02, "aligned_return": 0.02}},
        },
    ] * 5

    report = EventImpactBacktestService()._diagnostics(samples, "1d")

    assert report["dimensions"]["event_symbol"]["^SOX"]["miss_rate"] == 1.0
    assert report["dimensions"]["event_symbol"]["CL=F"]["miss_rate"] == 0.0
    symbol_segment = next(
        item for item in report["worst_segments"]
        if item["dimension"] == "event_symbol" and item["segment"] == "^SOX"
    )
    assert symbol_segment["miss_rate"] == 1.0

def test_calibration_status_compares_registered_baseline_without_samples():
    db = Session()
    baseline = BacktestRun(
        name="baseline", score_version="impact-v4", horizon="1d,5d",
        config={}, dataset_hash="b" * 64, parameter_adjustments=0,
        status="PARTIAL_ACCEPTANCE",
        report={"metrics": {
            "1d": {"observations": 100, "direction_hit_rate": 0.45,
                   "average_aligned_return": -0.001},
            "5d": {"observations": 100, "direction_hit_rate": 0.50,
                   "average_aligned_return": 0.001},
        }},
    )
    db.add(baseline)
    db.flush()
    current = BacktestRun(
        name="calibrated", score_version="impact-v4", horizon="1d,5d",
        config={"calibration_version": "impact-v4-calibration-1",
                "baseline_run_id": baseline.id},
        dataset_hash="c" * 64, parameter_adjustments=3,
        status="PARTIAL_ACCEPTANCE",
        report={
            "model_metadata": {
                "calibration_version": "impact-v4-calibration-1",
                "baseline_run_id": baseline.id,
            },
            "metrics": {
                "1d": {"observations": 101, "direction_hit_rate": 0.46,
                       "average_aligned_return": -0.0008},
                "5d": {"observations": 101, "direction_hit_rate": 0.52,
                       "average_aligned_return": 0.0015},
            },
            "samples": [{"must": "not leak"}],
        },
    )
    db.add(current)
    db.commit()

    response = backtests.event_impact_calibration_status(db)

    assert response["status"] == "COMPARABLE"
    assert response["current"]["run_id"] == current.id
    assert response["baseline"]["run_id"] == baseline.id
    assert response["comparison"]["5d"]["delta"] == {
        "direction_hit_rate": pytest.approx(0.02),
        "average_aligned_return": pytest.approx(0.0005),
    }
    assert "samples" not in response
    db.close()
