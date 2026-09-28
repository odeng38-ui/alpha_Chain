from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.main import app
from app.models.schema import BacktestRun, Company, DailyPrice, ScoreSnapshot, Security
from app.services.backtest_service import BacktestService, max_drawdown

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Session = sessionmaker(bind=engine)


@pytest.fixture(autouse=True)
def database():
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


def add_score(db, security, as_of, score):
    db.add(ScoreSnapshot(
        security_id=security.id, as_of_date=as_of, horizon="1d",
        component={"Macro": score}, total=score, confidence=0.8,
        version="v1.0", weights={"Macro": 1.0}, explanations={},
        completeness=1.0, config_hash="a" * 64,
    ))


def seed_backtest(db):
    securities = []
    for index in range(5):
        company = Company(name=f"기업{index}", industry_id="반도체")
        db.add(company)
        db.flush()
        security = Security(
            company_id=company.id, market="KOSPI", ticker=f"00000{index}",
            security_type="COMMON", listed_at=date(2020, 1, 1),
            delisted_at=date(2025, 12, 31) if index == 4 else None,
        )
        db.add(security)
        db.flush()
        securities.append(security)
    for period in range(5):
        as_of = date(2025, 1, 10) + timedelta(days=period * 10)
        for index, security in enumerate(securities):
            add_score(db, security, as_of, 100 - index * 15)
            prior = 100 + period
            entry = prior * (1.30 if index == 3 else 1.0)
            db.add(DailyPrice(
                security_id=security.id, trade_date=as_of,
                open=Decimal(str(prior)), close=Decimal(str(prior)),
            ))
            if index != 2 or period != 4:
                db.add(DailyPrice(
                    security_id=security.id, trade_date=as_of + timedelta(days=1),
                    open=Decimal(str(entry)), close=Decimal(str(entry)),
                ))
                db.add(DailyPrice(
                    security_id=security.id, trade_date=as_of + timedelta(days=2),
                    open=Decimal(str(entry * 1.02)), close=Decimal(str(entry * 1.02)),
                ))
    db.commit()
    return securities


def config(service):
    result = service.load_config()
    result.update({
        "quantiles": 2, "commission_bps": 10.0, "slippage_bps": 5.0,
        "parameter_adjustments": 2,
        "failure_conditions": {"minimum_test_observations": 1, "maximum_unfilled_rate": 0.5},
    })
    return result


def test_point_in_time_entry_delisted_and_execution_constraints():
    db = Session()
    securities = seed_backtest(db)
    service = BacktestService()
    cfg = config(service)
    score = db.query(ScoreSnapshot).filter_by(security_id=securities[4].id).first()
    delisted = service._sample(db, score, 1, cfg)
    assert delisted["filled"] is True
    assert delisted["entry_date"] > delisted["as_of_date"]
    limit_score = db.query(ScoreSnapshot).filter_by(security_id=securities[3].id).first()
    assert service._sample(db, limit_score, 1, cfg)["reason"] == "LIMIT_UP_ENTRY"


def test_costs_reduce_returns_and_drawdown_is_calculated():
    db = Session()
    securities = seed_backtest(db)
    service = BacktestService()
    score = db.query(ScoreSnapshot).filter_by(security_id=securities[0].id).first()
    cfg = config(service)
    with_cost = service._sample(db, score, 1, cfg)
    zero_cost = service._sample(db, score, 1, {**cfg, "commission_bps": 0, "slippage_bps": 0})
    assert with_cost["net_return"] < zero_cost["net_return"]
    assert max_drawdown([0.1, -0.2, 0.05]) == pytest.approx(-0.2)


def test_walk_forward_report_holdout_and_reproducible_dataset_hash():
    db = Session()
    seed_backtest(db)
    service = BacktestService()
    cfg = config(service)
    first = service.run(db, "first", horizon="1d", config_override=cfg)
    second = service.run(db, "second", horizon="1d", config_override=cfg)
    assert first.dataset_hash == second.dataset_hash
    assert first.train_end < first.validation_start < first.test_start
    assert first.report["final_holdout"] == first.report["metrics"]["test"]
    assert first.report["parameter_tuning"] == {"adjustments": 2, "used_holdout": False}
    assert first.report["execution_assumptions"]["commission_bps"] > 0
    assert first.report["bias_checklist"]["delisted_securities_included"] is True
    assert db.query(BacktestRun).count() == 2


def test_zero_commission_run_is_rejected():
    db = Session()
    with pytest.raises(ValueError):
        BacktestService().run(db, "invalid", horizon="1d", config_override={"commission_bps": 0})


def test_backtest_api_routes_are_registered():
    paths = app.openapi()["paths"]
    assert "/api/v1/backtests" in paths
    assert "/api/v1/backtests/{run_id}" in paths
