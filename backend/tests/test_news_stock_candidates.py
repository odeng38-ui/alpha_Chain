from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1.endpoints.news import list_stock_candidates
from app.db.session import Base
from app.models.schema import (
    Company,
    DailyPrice,
    NewsArticle,
    NewsClassification,
    NewsStockCandidate,
    Security,
)
from app.services.news_stock_candidate_service import NewsStockCandidateService

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Session = sessionmaker(bind=engine)


@pytest.fixture(autouse=True)
def database():
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


def add_security(db, name, ticker, industry, volume):
    company = Company(name=name, corp_code=ticker.zfill(8), status="ACTIVE", industry_id=industry)
    db.add(company)
    db.flush()
    security = Security(
        company_id=company.id, market="KOSPI", ticker=ticker,
        security_type="COMMON",
    )
    db.add(security)
    db.flush()
    db.add(DailyPrice(
        security_id=security.id, trade_date=date(2026, 10, 1),
        close=Decimal("100"), adjusted_close=Decimal("100"), volume=volume,
    ))
    return security


def add_classification(db, industries=None):
    article = NewsArticle(
        external_id="a" * 64, source="test", title="Chip stocks fall",
        url="https://example.com/chips", domain="example.com",
        published_at=datetime(2026, 10, 2, 12), raw_hash="b" * 64,
        raw_metadata={},
    )
    db.add(article)
    db.flush()
    classification = NewsClassification(
        news_article_id=article.id, event_kind="SEMICONDUCTOR",
        industries=industries or ["SEMICONDUCTORS_ELECTRONICS"],
        direction="NEGATIVE", confidence=0.8, matched_keywords=["chip", "fall"],
        rationale="test", review_required=False, version="news-rules-v1",
    )
    db.add(classification)
    db.flush()
    return article, classification


def test_candidates_link_industry_rank_by_pre_news_liquidity_and_are_idempotent():
    db = Session()
    first_security = add_security(
        db, "Liquid Chip", "000001", "SEMICONDUCTORS_ELECTRONICS", 1000,
    )
    second_security = add_security(
        db, "Thin Chip", "000002", "SEMICONDUCTORS_ELECTRONICS", 100,
    )
    missing_price_company = Company(
        name="No History Chip", corp_code="00000005", status="ACTIVE",
        industry_id="SEMICONDUCTORS_ELECTRONICS",
    )
    db.add(missing_price_company)
    db.flush()
    missing_price_security = Security(
        company_id=missing_price_company.id, market="KOSPI", ticker="000005",
        security_type="COMMON",
    )
    db.add(missing_price_security)
    db.flush()
    add_security(db, "Food", "000003", "FOOD_BEVERAGE", 10000)
    article, _ = add_classification(db)
    db.add(DailyPrice(
        security_id=second_security.id, trade_date=date(2026, 10, 3),
        close=Decimal("100"), adjusted_close=Decimal("100"), volume=999999,
    ))
    db.commit()

    service = NewsStockCandidateService()
    first = service.generate(db, article_id=article.id)
    second = service.generate(db, article_id=article.id)
    candidates = db.query(NewsStockCandidate).order_by(NewsStockCandidate.rank).all()

    assert first["created"] == 3
    assert second["processed"] == 0
    assert [item.security_id for item in candidates] == [
        first_security.id, second_security.id, missing_price_security.id,
    ]
    assert candidates[0].expected_direction == "NEGATIVE"
    assert candidates[0].explanation["price_trade_date"] == "2026-10-01"
    assert candidates[0].explanation["no_lookahead_cutoff"] == "2026-10-02"
    assert candidates[2].explanation["data_quality"] == "MISSING_PRE_NEWS_PRICE"
    db.close()


def test_regenerate_updates_without_duplicate_candidates():
    db = Session()
    add_security(db, "Chip", "000004", "SEMICONDUCTORS_ELECTRONICS", 1000)
    article, _ = add_classification(db)
    db.commit()
    service = NewsStockCandidateService()
    service.generate(db, article_id=article.id)
    result = service.generate(db, article_id=article.id, regenerate=True)
    assert result["updated"] == 1
    assert db.query(NewsStockCandidate).count() == 1
    db.close()

def test_balanced_selection_prevents_primary_industry_monopoly():
    db = Session()
    for index in range(10):
        add_security(db, f"Finance {index}", f"1{index:05d}", "FINANCIALS", 2000 - index)
        add_security(db, f"Industrial {index}", f"2{index:05d}", "INDUSTRIALS", 1000 - index)
    article, _ = add_classification(db, ["FINANCIALS", "INDUSTRIALS"])
    db.commit()

    result = NewsStockCandidateService().generate(
        db, article_id=article.id, candidates_per_article=10,
    )
    candidates = db.query(NewsStockCandidate).all()
    counts = {
        industry: sum(item.industry_id == industry for item in candidates)
        for industry in ("FINANCIALS", "INDUSTRIALS")
    }
    assert result["created"] == 10
    assert counts == {"FINANCIALS": 5, "INDUSTRIALS": 5}
    assert all(
        item.explanation["selection_strategy"] == "balanced_by_industry"
        for item in candidates
    )
    db.close()

def test_candidate_list_endpoint_query_executes():
    db = Session()
    add_security(db, "Chip", "000006", "SEMICONDUCTORS_ELECTRONICS", 1000)
    article, _ = add_classification(db)
    db.commit()
    NewsStockCandidateService().generate(db, article_id=article.id)
    response = list_stock_candidates(
        article_id=article.id, event_kind=None, limit=100, db=db,
    )
    assert response["count"] == 1
    assert response["data"][0]["ticker"] == "000006"
    assert response["data"][0]["article_url"] == article.url
    assert response["data"][0]["published_at"] == article.published_at.isoformat()
    assert response["data"][0]["source"] == article.source
    assert response["data"][0]["classification_rationale"]
    db.close()