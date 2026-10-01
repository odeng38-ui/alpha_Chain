from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models.schema import Company, DailyPrice, EventImpactCandidate, GlobalEvent, Security
from app.services.event_impact_service import EventImpactService
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