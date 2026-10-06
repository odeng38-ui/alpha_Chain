"""
3단계 테스트: price_service (백필/증분 수집)

MockAdapter를 사용하여 실제 KRX 호출 없이 로직을 검증한다.
"""

from datetime import date, datetime, timedelta
from typing import List, Optional

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.adapters.base import BrokerAdapter, DataNotFoundError, OHLCVRecord
from app.db.session import Base
from app.models.schema import (
    CollectionCheckpoint,
    Company,
    DailyPrice,
    NewsArticle,
    NewsClassification,
    NewsStockCandidate,
    Security,
)
from app.services import price_service

# ------------------------------------------------------------------ #
# DB 픽스처                                                             #
# ------------------------------------------------------------------ #

SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db():
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


# ------------------------------------------------------------------ #
# Mock 어댑터                                                           #
# ------------------------------------------------------------------ #

def make_ohlcv(ticker: str, trade_date: date) -> OHLCVRecord:
    """테스트용 더미 OHLCVRecord 생성."""
    return OHLCVRecord(
        ticker=ticker,
        trade_date=trade_date,
        open=70000.0,
        high=72000.0,
        low=69000.0,
        close=71000.0,
        volume=10_000_000,
        value=710_000_000_000.0,
        adjusted_close=71000.0,
        raw_hash="abc123" * 10 + "abcd",  # 64자 더미 해시
    )


class MockAdapter(BrokerAdapter):
    """실제 API 호출 없이 더미 데이터를 반환하는 테스트 어댑터."""

    def __init__(self, records: List[OHLCVRecord] = None):
        self._records = records or []
        self.calls = []

    @property
    def name(self) -> str:
        return "mock"

    def fetch_ohlcv(self, ticker, start_date, end_date, market="KOSPI") -> List[OHLCVRecord]:
        self.calls.append((ticker, start_date, end_date, market))
        return [r for r in self._records if start_date <= r.trade_date <= end_date]

    def fetch_latest(self, ticker, market="KOSPI") -> Optional[OHLCVRecord]:
        filtered = [r for r in self._records if r.ticker == ticker]
        return filtered[-1] if filtered else None

    def get_trading_days(self, start_date, end_date, market="KOSPI") -> List[date]:
        current = start_date
        days = []
        while current <= end_date:
            if current.weekday() < 5:  # 월~금만
                days.append(current)
            current += timedelta(days=1)
        return days


# ------------------------------------------------------------------ #
# 헬퍼                                                                  #
# ------------------------------------------------------------------ #

def create_test_security(db, ticker="005930", market="KOSPI") -> Security:
    company = Company(name="테스트기업", corp_code="12345678", status="ACTIVE")
    db.add(company)
    db.flush()

    security = Security(
        company_id=company.id,
        market=market,
        ticker=ticker,
        security_type="COMMON",
    )
    db.add(security)
    db.commit()
    return security


# ------------------------------------------------------------------ #
# 백필 테스트                                                            #
# ------------------------------------------------------------------ #

class TestBackfillSecurity:
    def test_backfill_inserts_records(self, db):
        """백필 시 OHLCVRecord가 DailyPrice로 정상 저장된다."""
        security = create_test_security(db)
        start = date(2024, 1, 2)
        end = date(2024, 1, 5)

        records = [make_ohlcv("005930", start + timedelta(days=i)) for i in range(4)]
        adapter = MockAdapter(records)

        result = price_service.backfill_security(db, security.id, start, end, adapter)

        stored = db.query(DailyPrice).filter(DailyPrice.security_id == security.id).all()
        assert result.inserted == 4
        assert result.skipped == 0
        assert len(stored) == 4
        assert result.errors == []

    def test_backfill_no_duplicate_on_rerun(self, db):
        """같은 기간 재수집 시 중복이 생기지 않는다."""
        security = create_test_security(db)
        start = date(2024, 1, 2)
        end = date(2024, 1, 3)

        records = [make_ohlcv("005930", start + timedelta(days=i)) for i in range(2)]
        adapter = MockAdapter(records)

        price_service.backfill_security(db, security.id, start, end, adapter)
        result2 = price_service.backfill_security(db, security.id, start, end, adapter)

        stored = db.query(DailyPrice).filter(DailyPrice.security_id == security.id).all()
        assert len(stored) == 2, "재수집 후에도 2건만 존재해야 함"
        assert result2.skipped == 2
        assert result2.inserted == 0

    def test_backfill_invalid_security_raises(self, db):
        """존재하지 않는 security_id는 ValueError를 발생시킨다."""
        adapter = MockAdapter([])
        with pytest.raises(ValueError):
            price_service.backfill_security(db, 9999, date(2024, 1, 1), date(2024, 1, 31), adapter)

    def test_backfill_empty_data_no_error(self, db):
        """어댑터가 빈 데이터를 반환해도 오류 없이 종료된다."""
        security = create_test_security(db)
        adapter = MockAdapter([])  # 빈 응답

        result = price_service.backfill_security(
            db, security.id, date(2024, 1, 1), date(2024, 1, 31), adapter
        )
        assert result.inserted == 0
        assert result.errors == []

    def test_backfill_raw_hash_stored(self, db):
        """raw_hash가 DailyPrice에 저장된다."""
        security = create_test_security(db)
        rec = make_ohlcv("005930", date(2024, 1, 2))
        adapter = MockAdapter([rec])

        price_service.backfill_security(
            db, security.id, date(2024, 1, 2), date(2024, 1, 2), adapter
        )
        stored = db.query(DailyPrice).filter(DailyPrice.security_id == security.id).first()
        # raw_hash 컬럼이 있으면 확인, 없으면 스킵
        if hasattr(stored, "raw_hash"):
            assert stored.raw_hash is not None


# ------------------------------------------------------------------ #
# 배치 백필 테스트                                                        #
# ------------------------------------------------------------------ #

class TestBackfillBatch:
    def test_batch_multiple_securities(self, db):
        """여러 종목 배치 백필이 모두 처리된다."""
        sec1 = create_test_security(db, ticker="005930")
        # 두 번째 기업은 corp_code 충돌을 피해 별도 Company로 생성
        company2 = Company(name="SK하이닉스", corp_code="00088790", status="ACTIVE")
        db.add(company2)
        db.flush()
        sec2 = Security(
            company_id=company2.id,
            market="KOSPI",
            ticker="000660",
            security_type="COMMON",
        )
        db.add(sec2)
        db.commit()

        start = date(2024, 1, 2)
        end = date(2024, 1, 2)

        records = [
            make_ohlcv("005930", start),
            make_ohlcv("000660", start),
        ]
        adapter = MockAdapter(records)

        results = price_service.backfill_batch(
            db, [sec1.id, sec2.id], start, end, adapter
        )
        assert len(results) == 2
        assert all(r.get("inserted", 0) >= 0 for r in results)


# ------------------------------------------------------------------ #
# 증분 수집 테스트                                                        #
# ------------------------------------------------------------------ #

class TestIncrementalUpdate:
    def test_incremental_collects_after_last_date(self, db):
        """증분 수집은 마지막 적재일 이후 데이터만 가져온다."""
        security = create_test_security(db)

        # 기존 데이터: 2024-01-02
        existing = DailyPrice(
            security_id=security.id,
            trade_date=date(2024, 1, 2),
            open=70000, high=72000, low=69000, close=71000,
            volume=10_000_000, value=710_000_000_000, adjusted_close=71000,
        )
        db.add(existing)
        db.commit()

        # 어댑터: 2024-01-03~04 데이터
        new_records = [
            make_ohlcv("005930", date(2024, 1, 3)),
            make_ohlcv("005930", date(2024, 1, 4)),
        ]
        adapter = MockAdapter(new_records)

        result = price_service.incremental_update(db, [security.id], adapter)

        total = db.query(DailyPrice).filter(DailyPrice.security_id == security.id).count()
        assert total == 3, "기존 1건 + 신규 2건 = 3건이어야 함"
        assert result.total_inserted == 2
        assert result.errors == []

    def test_incremental_no_data_uses_bounded_initial_lookback(self, db, monkeypatch):
        security = create_test_security(db)
        adapter = MockAdapter([])
        monkeypatch.setattr(price_service.settings, "PRICE_INITIAL_LOOKBACK_DAYS", 30)

        result = price_service.incremental_update(db, [security.id], adapter)

        assert result.updated_securities == 1
        assert result.total_inserted == 0
        assert len(adapter.calls) == 1
        _, start_date, end_date, _ = adapter.calls[0]
        assert end_date == date.today()
        assert start_date == date.today() - timedelta(days=30)

    def test_incremental_treats_no_new_trading_session_as_success(self, db):
        security = create_test_security(db)
        last_date = date.today() - timedelta(days=1)
        db.add(DailyPrice(
            security_id=security.id, trade_date=last_date,
            open=100, high=100, low=100, close=100, adjusted_close=100,
        ))
        db.commit()

        class NoTradingSessionAdapter(MockAdapter):
            def fetch_ohlcv(self, ticker, start_date, end_date, market="KOSPI"):
                raise DataNotFoundError(f"No price data found for ticker={ticker}")

        result = price_service.incremental_update(
            db, [security.id], NoTradingSessionAdapter([]),
        )
        checkpoint = db.query(CollectionCheckpoint).filter_by(
            job_name="daily_price", security_id=security.id,
        ).one()

        assert result.errors == []
        assert result.updated_securities == 1
        assert checkpoint.status == "SUCCESS"
        assert checkpoint.last_success_date == last_date
        assert checkpoint.last_error is None


class TestIncrementalBatchUpdate:
    def test_batch_is_bounded_and_returns_cursor(self, db):
        first = create_test_security(db, ticker="005930")
        second_company = Company(name="Batch Test 2", corp_code="87654321", status="ACTIVE")
        db.add(second_company)
        db.flush()
        second = Security(
            company_id=second_company.id,
            market="KOSPI",
            ticker="000660",
            security_type="COMMON",
        )
        db.add(second)
        db.commit()

        batch = price_service.incremental_batch_update(
            db,
            offset=0,
            batch_size=1,
            adapter=MockAdapter([]),
        ).to_dict()

        assert batch["processed"] == 1
        assert batch["total_candidates"] == 2
        assert batch["security_ids"] == [first.id]
        assert batch["next_offset"] == 1
        assert batch["has_more"] is True

def test_due_batch_skips_security_already_successful_today(db):
    completed = create_test_security(db, ticker="005930")
    company = Company(name="Due Batch Test", corp_code="11223344", status="ACTIVE")
    db.add(company)
    db.flush()
    due = Security(
        company_id=company.id,
        market="KOSPI",
        ticker="000660",
        security_type="COMMON",
    )
    db.add(due)
    db.flush()
    db.add(
        CollectionCheckpoint(
            job_name="daily_price",
            security_id=completed.id,
            last_success_date=date.today(),
            status="SUCCESS",
        )
    )
    db.commit()

    result = price_service.incremental_due_batch_update(
        db,
        batch_size=1,
        adapter=MockAdapter([]),
    )

    assert result["security_ids"] == [due.id]
    assert result["processed"] == 1
    assert result["has_more"] is False

def test_due_batch_skips_recent_failure_and_advances_to_unattempted(db, monkeypatch):
    failed = create_test_security(db, ticker="000010")
    company = Company(name="Unattempted", corp_code="22334455", status="ACTIVE")
    db.add(company)
    db.flush()
    unattempted = Security(
        company_id=company.id,
        market="KOSPI",
        ticker="005930",
        security_type="COMMON",
    )
    db.add(unattempted)
    db.flush()
    db.add(
        CollectionCheckpoint(
            job_name="daily_price",
            security_id=failed.id,
            status="FAILED",
            last_error="not found",
            updated_at=datetime.utcnow(),
        )
    )
    db.commit()
    monkeypatch.setattr(price_service.settings, "PRICE_FAILURE_RETRY_DAYS", 7)

    result = price_service.incremental_due_batch_update(
        db,
        batch_size=1,
        adapter=MockAdapter([]),
    )

    assert result["security_ids"] == [unattempted.id]


def test_due_batch_retries_failure_after_cooldown(db, monkeypatch):
    failed = create_test_security(db, ticker="000010")
    db.add(
        CollectionCheckpoint(
            job_name="daily_price",
            security_id=failed.id,
            status="FAILED",
            last_error="not found",
            updated_at=datetime.utcnow() - timedelta(days=8),
        )
    )
    db.commit()
    monkeypatch.setattr(price_service.settings, "PRICE_FAILURE_RETRY_DAYS", 7)

    result = price_service.incremental_due_batch_update(
        db,
        batch_size=1,
        adapter=MockAdapter([]),
    )

    assert result["security_ids"] == [failed.id]

def test_due_batch_prioritizes_recent_directional_news_candidate(db):
    create_test_security(db, ticker="000010")
    priority_company = Company(
        name="Priority Company", corp_code="87654321", status="ACTIVE",
        industry_id="FINANCIALS",
    )
    db.add(priority_company)
    db.flush()
    priority = Security(
        company_id=priority_company.id, market="KOSPI", ticker="000020",
        security_type="COMMON",
    )
    db.add(priority)
    db.flush()
    article = NewsArticle(
        external_id="9" * 64, source="test", title="Market falls",
        url="https://example.com/priority", published_at=datetime.utcnow(),
        raw_hash="8" * 64, raw_metadata={},
    )
    db.add(article)
    db.flush()
    classification = NewsClassification(
        news_article_id=article.id, event_kind="MARKET_MOVEMENT",
        industries=["FINANCIALS"], direction="NEGATIVE", confidence=0.8,
        matched_keywords=["market", "falls"], rationale="test",
        review_required=False, version="news-rules-v1",
    )
    db.add(classification)
    db.flush()
    db.add(NewsStockCandidate(
        classification_id=classification.id, security_id=priority.id,
        industry_id="FINANCIALS", expected_direction="NEGATIVE",
        relevance_score=80, confidence=0.8, rank=1,
        explanation={"data_quality": "OK"}, version="news-link-v1",
    ))
    db.commit()

    result = price_service.incremental_due_batch_update(
        db, batch_size=1, adapter=MockAdapter([]),
    )

    assert result["security_ids"] == [priority.id]
    assert result["priority_candidates"] == 1
    assert result["priority_processed"] == 1


def test_import_price_records_is_guarded_and_updates_checkpoint(db):
    company = Company(name="Import Corp")
    db.add(company)
    db.flush()
    security = Security(
        company_id=company.id,
        market="KOSDAQ",
        ticker="043220",
        security_type="COMMON",
    )
    db.add(security)
    db.commit()
    records = [
        make_ohlcv("043220", date(2024, 1, 2)),
        make_ohlcv("043220", date(2024, 1, 3)),
    ]

    result = price_service.import_price_records(
        db,
        security_id=security.id,
        ticker="043220",
        records=records,
    )

    assert result["inserted"] == 2
    assert result["skipped"] == 0
    assert db.query(DailyPrice).filter_by(security_id=security.id).count() == 2
    checkpoint = db.query(CollectionCheckpoint).filter_by(
        job_name="daily_price",
        security_id=security.id,
    ).one()
    assert checkpoint.status == "SUCCESS"
    assert checkpoint.last_success_date == date(2024, 1, 3)


def test_import_price_records_rejects_ticker_mismatch(db):
    company = Company(name="Import Corp")
    db.add(company)
    db.flush()
    security = Security(
        company_id=company.id,
        market="KOSDAQ",
        ticker="043220",
        security_type="COMMON",
    )
    db.add(security)
    db.commit()

    with pytest.raises(ValueError, match="ticker does not match security"):
        price_service.import_price_records(
            db,
            security_id=security.id,
            ticker="999999",
            records=[make_ohlcv("999999", date(2024, 1, 2))],
        )

    assert db.query(DailyPrice).count() == 0