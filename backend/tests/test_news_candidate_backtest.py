from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models.schema import (
    Company,
    DailyPrice,
    NewsArticle,
    NewsCandidateValidationRun,
    NewsClassification,
    NewsStockCandidate,
    Security,
)
from app.services.news_candidate_backtest_service import NewsCandidateBacktestService

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Session = sessionmaker(bind=engine)


@pytest.fixture(autouse=True)
def database():
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


def add_candidate_dataset(db):
    company = Company(
        name="Test", corp_code="00000001", status="ACTIVE", industry_id="FINANCIALS",
    )
    db.add(company)
    db.flush()
    security = Security(
        company_id=company.id, market="KOSPI", ticker="000001", security_type="COMMON",
    )
    db.add(security)
    db.flush()
    article = NewsArticle(
        external_id="a" * 64, source="test", title="Positive market news",
        url="https://example.com/news", published_at=datetime(2026, 1, 2, 1),
        raw_hash="b" * 64, raw_metadata={},
    )
    db.add(article)
    db.flush()
    classification = NewsClassification(
        news_article_id=article.id, event_kind="MARKET_MOVEMENT",
        industries=["FINANCIALS"], direction="POSITIVE", confidence=0.8,
        matched_keywords=["market"], rationale="test", review_required=False,
        version="news-rules-v1",
    )
    db.add(classification)
    db.flush()
    candidate = NewsStockCandidate(
        classification_id=classification.id, security_id=security.id,
        industry_id="FINANCIALS", expected_direction="POSITIVE",
        relevance_score=80, confidence=0.8, rank=1,
        explanation={"data_quality": "OK"}, version="news-link-v1",
    )
    db.add(candidate)
    for index in range(6):
        trade_date = date(2026, 1, 2) + timedelta(days=index)
        db.add(DailyPrice(
            security_id=security.id, trade_date=trade_date,
            open=Decimal("100"), close=Decimal(str(101 + index)),
            adjusted_close=Decimal(str(101 + index)), volume=1000,
        ))
    db.commit()
    return candidate


def test_backtest_requires_passed_candidate_validation():
    db = Session()
    add_candidate_dataset(db)
    with pytest.raises(ValueError, match="must be PASSED"):
        NewsCandidateBacktestService().run(db, "blocked")
    db.close()


def test_backtest_uses_next_korean_session_open_and_marks_small_sample():
    db = Session()
    add_candidate_dataset(db)
    db.add(NewsCandidateValidationRun(
        version="news-candidate-acceptance-v1", status="PASSED",
        as_of=date(2026, 1, 10), report={}, evaluated_at=datetime(2026, 1, 10),
    ))
    db.commit()
    run = NewsCandidateBacktestService().run(db, "news outcomes", horizons=(1, 5, 20))
    sample = run.report["samples"][0]
    assert run.status == "INSUFFICIENT_SAMPLE"
    assert sample["outcomes"]["1d"]["entry_date"] == "2026-01-03"
    assert sample["outcomes"]["1d"]["raw_return"] == pytest.approx(0.02)
    assert sample["outcomes"]["5d"]["exit_date"] == "2026-01-07"
    assert sample["outcomes"]["20d"] is None
    assert run.report["metrics"]["1d"]["observations"] == 1
    assert run.report["horizon_acceptance"]["1d"]["status"] == "INSUFFICIENT_SAMPLE"
    db.close()

def test_benchmark_uses_full_market_and_same_industry_universe():
    db = Session()
    for index, close in enumerate(("102", "100"), 1):
        company = Company(
            name=f"Benchmark {index}", corp_code=f"{index:08d}",
            status="ACTIVE", industry_id="FINANCIALS",
        )
        db.add(company)
        db.flush()
        security = Security(
            company_id=company.id, market="KOSPI", ticker=f"{index:06d}",
            security_type="COMMON",
        )
        db.add(security)
        db.flush()
        db.add(DailyPrice(
            security_id=security.id, trade_date=date(2026, 1, 3),
            open=Decimal("100"), close=Decimal("100"), adjusted_close=Decimal("100"),
        ))
        db.add(DailyPrice(
            security_id=security.id, trade_date=date(2026, 1, 4),
            open=Decimal("100"), close=Decimal(close), adjusted_close=Decimal(close),
        ))
    db.commit()
    result = NewsCandidateBacktestService._benchmark(
        db, date(2026, 1, 3), date(2026, 1, 4),
    )
    assert result["market_count"] == 2
    assert result["market_return"] == pytest.approx(0.01)
    assert result["industry_returns"]["FINANCIALS"] == pytest.approx(0.01)
    db.close()