from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models.schema import Company, DailyPrice, EventImpactCandidate, GlobalEvent, Security
from app.services.event_impact_service import (
    EventImpactService,
    EventImpactV2Service,
    EventImpactV3Service,
)
from app.services.industry_service import sync_industry_batch

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Session = sessionmaker(bind=engine)


@pytest.fixture(autouse=True)
def database():
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


def add_security(db, name, industry, ticker, price_date):
    company = Company(name=name, corp_code=ticker.zfill(8), industry_id=industry)
    db.add(company)
    db.flush()
    security = Security(company_id=company.id, market="KOSPI", ticker=ticker, security_type="COMMON")
    db.add(security)
    db.flush()
    db.add(DailyPrice(security_id=security.id, trade_date=price_date, close=Decimal("100"), adjusted_close=Decimal("100")))
    return security


def test_candidates_are_ranked_explainable_and_idempotent():
    db = Session()
    event_date = date(2025, 2, 3)
    semiconductor = add_security(db, "반도체", "SEMICONDUCTORS_ELECTRONICS", "000001", event_date)
    electrical = add_security(db, "전기", "ELECTRICAL_EQUIPMENT", "000002", event_date - timedelta(days=2))
    add_security(db, "식품", "FOOD_BEVERAGE", "000003", event_date)
    event = GlobalEvent(
        external_id="test:sox", source="TEST", origin_country="US", event_kind="MARKET_SHOCK",
        symbol="^SOX", title="SOX shock", direction="NEGATIVE",
        occurred_at=datetime(2025, 2, 2), available_at=datetime.combine(event_date, datetime.min.time()),
        shock_score=80, event_metadata={}, raw_hash="a" * 64,
    )
    db.add(event)
    db.commit()
    first = EventImpactService().generate(db, event.id)
    second = EventImpactService().generate(db, event.id)
    rows = db.query(EventImpactCandidate).order_by(EventImpactCandidate.rank).all()
    assert first["created"] == 2 and second["created"] == 0
    assert [row.security_id for row in rows] == [semiconductor.id, electrical.id]
    assert rows[0].impact_score < 0
    assert rows[0].explanation["formula"].startswith("signed(")
    db.close()


class MockDartAdapter:
    def company_profile(self, corp_code):
        return {"induty_code": "26110" if corp_code == "00000001" else "62010"}


def test_industry_batch_uses_cursor_and_classifies():
    db = Session()
    db.add_all([Company(name="A", corp_code="00000001"), Company(name="B", corp_code="00000002")])
    db.commit()
    first = sync_industry_batch(db, MockDartAdapter(), batch_size=1)
    second = sync_industry_batch(db, MockDartAdapter(), after_id=first["next_after_id"], batch_size=1)
    assert first["updated"] == 1 and second["updated"] == 1
    assert {row.industry_id for row in db.query(Company).all()} == {"SEMICONDUCTORS_ELECTRONICS", "SOFTWARE_IT"}
    db.close()

def test_v2_uses_only_prior_events_and_pre_event_prices():
    db = Session()
    company = Company(name="Sensitive", corp_code="99999999", industry_id="SEMICONDUCTORS_ELECTRONICS")
    db.add(company)
    db.flush()
    security = Security(company_id=company.id, market="KOSPI", ticker="999999", security_type="COMMON")
    db.add(security)
    db.flush()
    for trade_date, close in [
        (date(2025, 1, 1), "100"), (date(2025, 1, 3), "110"),
        (date(2025, 1, 9), "110"), (date(2025, 1, 10), "121"),
        (date(2025, 2, 9), "121"), (date(2025, 2, 10), "999"),
    ]:
        db.add(DailyPrice(security_id=security.id, trade_date=trade_date,
                          close=Decimal(close), adjusted_close=Decimal(close)))
    events = [
        GlobalEvent(external_id="prior-1", source="TEST", origin_country="US",
                    event_kind="MARKET_SHOCK", symbol="^SOX", title="prior 1",
                    direction="POSITIVE", occurred_at=datetime(2025, 1, 2),
                    available_at=datetime(2025, 1, 3), shock_score=70,
                    event_metadata={}, raw_hash="1" * 64),
        GlobalEvent(external_id="prior-2", source="TEST", origin_country="US",
                    event_kind="MARKET_SHOCK", symbol="^SOX", title="prior 2",
                    direction="POSITIVE", occurred_at=datetime(2025, 1, 9),
                    available_at=datetime(2025, 1, 10), shock_score=70,
                    event_metadata={}, raw_hash="2" * 64),
        GlobalEvent(external_id="target", source="TEST", origin_country="US",
                    event_kind="MARKET_SHOCK", symbol="^SOX", title="target",
                    direction="POSITIVE", occurred_at=datetime(2025, 2, 9),
                    available_at=datetime(2025, 2, 10), shock_score=80,
                    event_metadata={}, raw_hash="3" * 64),
        GlobalEvent(external_id="future", source="TEST", origin_country="US",
                    event_kind="MARKET_SHOCK", symbol="^SOX", title="future",
                    direction="POSITIVE", occurred_at=datetime(2025, 2, 19),
                    available_at=datetime(2025, 2, 20), shock_score=80,
                    event_metadata={}, raw_hash="4" * 64),
    ]
    db.add_all(events)
    db.commit()
    result = EventImpactV2Service().generate(db, events[2].id)
    row = db.query(EventImpactCandidate).filter_by(version="impact-v2").one()
    sensitivity = row.explanation["historical_sensitivity"]
    assert result["historical_event_count"] == 2
    assert sensitivity["sample_count"] == 2
    assert sensitivity["aligned_mean_return"] == pytest.approx(0.1)
    assert row.explanation["price_trade_date"] == "2025-02-09"
    assert row.explanation["no_lookahead_cutoff"] == "2025-02-10T00:00:00"
    db.close()

def test_v3_removes_market_effect_and_uses_liquidity():
    db = Session()
    securities = []
    for index, industry in enumerate(("SEMICONDUCTORS_ELECTRONICS", "SEMICONDUCTORS_ELECTRONICS"), 1):
        company = Company(name=f"V3-{index}", corp_code=f"{index:08d}", industry_id=industry)
        db.add(company)
        db.flush()
        security = Security(company_id=company.id, market="KOSPI", ticker=f"{index:06d}", security_type="COMMON")
        db.add(security)
        db.flush()
        securities.append(security)
        closes = (Decimal("100"), Decimal("110")) if index == 1 else (Decimal("100"), Decimal("102"))
        db.add(DailyPrice(security_id=security.id, trade_date=date(2025, 1, 2),
                          close=closes[0], adjusted_close=closes[0], volume=1000 * index))
        db.add(DailyPrice(security_id=security.id, trade_date=date(2025, 1, 3),
                          close=closes[1], adjusted_close=closes[1], volume=1000 * index))
        db.add(DailyPrice(security_id=security.id, trade_date=date(2025, 2, 9),
                          close=closes[1], adjusted_close=closes[1], volume=1000 * index))
    prior = GlobalEvent(external_id="v3-prior", source="TEST", origin_country="US",
                        event_kind="MARKET_SHOCK", symbol="^SOX", title="prior",
                        direction="POSITIVE", occurred_at=datetime(2025, 1, 2),
                        available_at=datetime(2025, 1, 3), shock_score=70,
                        event_metadata={}, raw_hash="5" * 64)
    target = GlobalEvent(external_id="v3-target", source="TEST", origin_country="US",
                         event_kind="MARKET_SHOCK", symbol="^SOX", title="target",
                         direction="POSITIVE", occurred_at=datetime(2025, 2, 9),
                         available_at=datetime(2025, 2, 10), shock_score=70,
                         event_metadata={}, raw_hash="6" * 64)
    db.add_all([prior, target])
    db.commit()
    result = EventImpactV3Service().generate(db, target.id, 2)
    rows = db.query(EventImpactCandidate).filter_by(version="impact-v3").order_by(EventImpactCandidate.rank).all()
    assert result["count"] == 2
    assert rows[0].security_id == securities[0].id
    assert rows[0].explanation["market_adjustment"]["abnormal_aligned_return"] > 0
    assert rows[1].explanation["market_adjustment"]["abnormal_aligned_return"] < 0
    assert rows[1].explanation["liquidity"]["percentile"] == 1.0
    db.close()
