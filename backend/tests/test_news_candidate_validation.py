from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models.schema import (
    Company,
    NewsArticle,
    NewsCandidateValidationRun,
    NewsClassification,
    NewsStockCandidate,
    Security,
)
from app.services.news_candidate_validation_service import NewsCandidateValidationService

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Session = sessionmaker(bind=engine)


@pytest.fixture(autouse=True)
def database():
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


def add_dataset(db, published_at, count, quality="OK"):
    article = NewsArticle(
        external_id=(str(published_at).encode().hex() + "0" * 64)[:64],
        source="test", title="Market news", url="https://example.com/news",
        published_at=published_at, raw_hash="a" * 64, raw_metadata={},
    )
    db.add(article)
    db.flush()
    classification = NewsClassification(
        news_article_id=article.id, event_kind="MARKET_MOVEMENT",
        industries=["FINANCIALS", "INDUSTRIALS"], direction="NEGATIVE",
        confidence=0.8, matched_keywords=["market"], rationale="test",
        review_required=False, version="news-rules-v1",
    )
    db.add(classification)
    db.flush()
    for index in range(count):
        industry = "FINANCIALS" if index < 7 else "INDUSTRIALS"
        company = Company(
            name=f"Company {article.id}-{index}", corp_code=f"{article.id:04d}{index:04d}",
            status="ACTIVE", industry_id=industry,
        )
        db.add(company)
        db.flush()
        security = Security(
            company_id=company.id, market="KOSPI", ticker=f"{article.id:02d}{index:04d}",
            security_type="COMMON",
        )
        db.add(security)
        db.flush()
        db.add(NewsStockCandidate(
            classification_id=classification.id, security_id=security.id,
            industry_id=industry, expected_direction="NEGATIVE",
            relevance_score=80 - index, confidence=0.8, rank=index + 1,
            explanation={"data_quality": quality}, version="news-link-v1",
        ))
    db.commit()


def test_validation_passes_balanced_fresh_supported_candidates():
    db = Session()
    as_of = date(2026, 10, 2)
    add_dataset(db, datetime.combine(as_of - timedelta(days=1), datetime.min.time()), 10)
    result = NewsCandidateValidationService().validate(db, as_of)
    assert result["status"] == "PASSED"
    assert result["report"]["failed_checks"] == []
    assert db.query(NewsCandidateValidationRun).count() == 1
    db.close()


def test_validation_fails_stale_missing_price_and_shallow_candidates():
    db = Session()
    as_of = date(2026, 10, 2)
    add_dataset(
        db, datetime.combine(as_of - timedelta(days=30), datetime.min.time()),
        1, quality="MISSING_PRE_NEWS_PRICE",
    )
    result = NewsCandidateValidationService().validate(db, as_of)
    assert result["status"] == "FAILED"
    assert set(result["report"]["failed_checks"]) >= {
        "fresh_articles", "price_evidence", "article_coverage", "candidate_depth",
    }
    db.close()

def test_validation_preserves_history_but_scores_only_active_window():
    db = Session()
    as_of = date(2026, 10, 2)
    add_dataset(
        db, datetime.combine(as_of - timedelta(days=30), datetime.min.time()),
        1, quality="MISSING_PRE_NEWS_PRICE",
    )
    add_dataset(db, datetime.combine(as_of - timedelta(days=1), datetime.min.time()), 10)

    result = NewsCandidateValidationService().validate(db, as_of)

    assert result["status"] == "PASSED"
    scope = result["report"]["scope"]
    assert scope["historical_classifications"] == 2
    assert scope["active_total_classifications"] == 1
    assert scope["active_eligible_classifications"] == 1
    assert scope["historical_candidates"] == 11
    assert scope["active_candidates"] == 10
    assert db.query(NewsClassification).count() == 2
    assert db.query(NewsStockCandidate).count() == 11
    db.close()

def test_validation_excludes_unlinkable_classification_from_coverage():
    db = Session()
    as_of = date(2026, 10, 2)
    published_at = datetime.combine(as_of - timedelta(days=1), datetime.min.time())
    add_dataset(db, published_at, 10)
    article = NewsArticle(
        external_id="f" * 64, source="test", title="Unclassified news",
        url="https://example.com/other", published_at=published_at,
        raw_hash="e" * 64, raw_metadata={},
    )
    db.add(article)
    db.flush()
    db.add(NewsClassification(
        news_article_id=article.id, event_kind="OTHER", industries=[],
        direction="NEUTRAL", confidence=0.3, matched_keywords=[],
        rationale="no match", review_required=True, version="news-rules-v1",
    ))
    db.commit()

    result = NewsCandidateValidationService().validate(db, as_of)

    assert result["status"] == "PASSED"
    scope = result["report"]["scope"]
    assert scope["active_total_classifications"] == 2
    assert scope["active_eligible_classifications"] == 1
    assert scope["active_excluded_classifications"] == 1
    assert result["report"]["metrics"]["article_coverage_rate"] == 1.0
    db.close()
