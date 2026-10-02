from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.adapters.gdelt_news_adapter import GdeltNewsAdapter, NewsArticleRecord
from app.db.session import Base
from app.models.schema import NewsArticle
from app.services.news_collection_service import NewsCollectionService

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Session = sessionmaker(bind=engine)


@pytest.fixture(autouse=True)
def database():
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


class MockAdapter:
    def __init__(self):
        self.hash = "a" * 64

    def fetch(self, timespan="24h", max_records=100):
        return [NewsArticleRecord(
            source="Mock", external_id="b" * 64, title="Federal Reserve holds rates",
            url="https://example.com/fed", domain="example.com",
            language="English", source_country="United States",
            published_at=datetime(2026, 10, 1, 12), image_url=None,
            raw_hash=self.hash, raw_metadata={"timespan": timespan},
        )]


def test_news_sync_is_idempotent_and_updates_changed_source():
    db = Session()
    adapter = MockAdapter()
    service = NewsCollectionService(adapter)
    first = service.sync(db)
    second = service.sync(db)
    adapter.hash = "c" * 64
    third = service.sync(db)
    assert first["created"] == 1
    assert second["unchanged"] == 1
    assert third["updated"] == 1
    assert db.query(NewsArticle).count() == 1
    db.close()


def test_gdelt_date_parser_is_utc_naive_for_database_consistency():
    assert GdeltNewsAdapter._published("20261001T123000Z") == datetime(2026, 10, 1, 12, 30)


def test_gdelt_query_targets_us_english_market_news():
    query = GdeltNewsAdapter.default_query
    assert "sourcecountry:US" in query
    assert "sourcelang:english" in query
    assert "Federal Reserve" in query

def test_gdelt_retries_rate_limit(monkeypatch):
    class Response:
        def __init__(self, status_code, payload=None):
            self.status_code = status_code
            self.headers = {}
            self.payload = payload or {}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError("unexpected status")

        def json(self):
            return self.payload

    responses = iter([Response(429), Response(200, {"articles": []})])
    calls = []
    monkeypatch.setattr("app.adapters.gdelt_news_adapter.httpx.get",
                        lambda *args, **kwargs: next(responses))
    monkeypatch.setattr("app.adapters.gdelt_news_adapter.time.sleep",
                        lambda delay: calls.append(delay))

    assert GdeltNewsAdapter(max_attempts=2).fetch(max_records=1) == []
    assert calls == [1]