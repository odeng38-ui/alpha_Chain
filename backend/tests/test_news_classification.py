from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models.schema import NewsArticle, NewsClassification
from app.services.news_classification_service import NewsClassificationService

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Session = sessionmaker(bind=engine)


@pytest.fixture(autouse=True)
def database():
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


def article(title):
    return NewsArticle(
        external_id=(title.encode().hex() + "0" * 64)[:64], source="test",
        title=title, url="https://example.com/news", domain="example.com",
        published_at=datetime(2026, 10, 2), raw_hash="a" * 64,
        raw_metadata={},
    )


def test_classifies_fed_policy_direction_and_industries():
    result = NewsClassificationService.classify_text(
        "Federal Reserve rate hike sends stocks lower"
    )
    assert result.event_kind == "FED_POLICY"
    assert result.direction == "NEGATIVE"
    assert "FINANCIALS" in result.industries
    assert result.confidence >= 0.65


def test_classifies_mixed_semiconductor_energy_market_news():
    result = NewsClassificationService.classify_text(
        "World stocks fall in semiconductor rout; oil rises"
    )
    assert result.event_kind == "SEMICONDUCTOR"
    assert result.direction == "MIXED"
    assert "SEMICONDUCTORS_ELECTRONICS" in result.industries
    assert "ENERGY_CHEMICALS" in result.industries


def test_unknown_news_requires_review_and_sync_is_idempotent():
    db = Session()
    db.add(article("Local community holds annual festival"))
    db.commit()
    service = NewsClassificationService()
    first = service.classify_pending(db)
    second = service.classify_pending(db)
    assert first == {"version": "news-rules-v1", "processed": 1,
                     "created": 1, "updated": 0, "review_required": 1}
    assert second["processed"] == 0
    row = db.query(NewsClassification).one()
    assert row.event_kind == "OTHER"
    assert row.review_required is True
    db.close()


def test_reclassify_updates_existing_row_without_duplication():
    db = Session()
    db.add(article("Inflation rise pressures the stock market"))
    db.commit()
    service = NewsClassificationService()
    service.classify_pending(db)
    result = service.classify_pending(db, reclassify=True)
    assert result["updated"] == 1
    assert db.query(NewsClassification).count() == 1
    db.close()