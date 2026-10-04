from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.adapters.us_market_adapter import USMarketRecord
from app.db.session import Base
from app.models.schema import GlobalEvent
from app.services.us_market_event_service import USMarketEventService

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Session = sessionmaker(bind=engine)


@pytest.fixture(autouse=True)
def database():
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


class MockAdapter:
    def fetch_daily(self, symbol, start_date, end_date):
        origin = date(2025, 1, 1)
        closes = [100 + index * 0.1 for index in range(21)] + [108]
        return [USMarketRecord(symbol, origin + timedelta(days=index), close, 1000)
                for index, close in enumerate(closes)]


def test_detect_uses_prior_window_and_finds_shock():
    records = MockAdapter().fetch_daily("^GSPC", date(2025, 1, 1), date(2025, 2, 1))
    events = USMarketEventService.detect("^GSPC", records)
    assert len(events) == 1
    assert events[0]["direction"] == "POSITIVE"
    assert events[0]["zscore_20d"] is not None
    assert events[0]["shock_score"] >= 50


def test_sync_is_idempotent_and_sets_next_day_availability():
    db = Session()
    service = USMarketEventService(MockAdapter())
    first = service.sync(db, date(2025, 2, 1), symbols=("^GSPC",))
    second = service.sync(db, date(2025, 2, 1), symbols=("^GSPC",))
    assert first["created"] == 1
    assert first["event_ids"]
    assert second["event_ids"] == []
    assert second["created"] == 0
    assert db.query(GlobalEvent).count() == 1
    row = db.query(GlobalEvent).one()
    assert row.available_at.date() == row.occurred_at.date() + timedelta(days=1)
    db.close()