from datetime import date, datetime, timezone

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


def test_google_news_repairs_mojibake():
    from app.adapters.google_news_adapter import GoogleNewsRssAdapter

    broken = "stocks \u00e2\u0080\u0094 bonds"
    assert GoogleNewsRssAdapter._repair_text(broken) == "stocks \u2014 bonds"


def test_google_news_enforces_requested_publication_window(monkeypatch):
    from app.adapters.google_news_adapter import GoogleNewsRssAdapter

    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <rss><channel>
      <item><title>Fresh market news - Reuters</title><link>https://fresh</link>
        <pubDate>Fri, 02 Oct 2026 11:00:00 GMT</pubDate>
        <source url="https://reuters.com">Reuters</source><guid>fresh</guid></item>
      <item><title>Old market news - Reuters</title><link>https://old</link>
        <pubDate>Tue, 01 Sep 2026 11:00:00 GMT</pubDate>
        <source url="https://reuters.com">Reuters</source><guid>old</guid></item>
    </channel></rss>"""

    class Response:
        content = xml

        @staticmethod
        def raise_for_status():
            return None

    monkeypatch.setattr(
        "app.adapters.google_news_adapter.httpx.get",
        lambda *args, **kwargs: Response(),
    )
    adapter = GoogleNewsRssAdapter(now=datetime(2026, 10, 2, 12, tzinfo=timezone.utc))
    rows = adapter.fetch(timespan="24h")
    assert [row.title for row in rows] == ["Fresh market news - Reuters"]


def test_prune_stale_supports_dry_run_and_precise_cutoff():
    db = Session()
    db.add_all([
        NewsArticle(
            external_id="c" * 64, source="test", title="stale", url="https://old",
            published_at=datetime(2026, 9, 20), raw_hash="d" * 64, raw_metadata={},
        ),
        NewsArticle(
            external_id="e" * 64, source="test", title="fresh", url="https://fresh",
            published_at=datetime(2026, 10, 1), raw_hash="f" * 64, raw_metadata={},
        ),
    ])
    db.commit()
    preview = NewsCollectionService.prune_stale(
        db, max_age_days=7, as_of=date(2026, 10, 2), dry_run=True,
    )
    assert preview["articles"] == 1
    assert db.query(NewsArticle).count() == 2
    applied = NewsCollectionService.prune_stale(
        db, max_age_days=7, as_of=date(2026, 10, 2), dry_run=False,
    )
    assert applied["articles"] == 1
    assert db.query(NewsArticle).one().title == "fresh"
    db.close()