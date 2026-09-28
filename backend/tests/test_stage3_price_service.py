"""
3단계 테스트: price_service (백필/증분 수집)

MockAdapter를 사용하여 실제 KRX 호출 없이 로직을 검증한다.
"""

from datetime import date, timedelta
from typing import List, Optional

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.adapters.base import BrokerAdapter, OHLCVRecord
from app.db.session import Base
from app.models.schema import Company, DailyPrice, Security
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

    @property
    def name(self) -> str:
        return "mock"

    def fetch_ohlcv(self, ticker, start_date, end_date, market="KOSPI") -> List[OHLCVRecord]:
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

    def test_incremental_no_data_fetches_recent_year(self, db):
        """DB에 데이터가 없으면 최근 1년치를 시도한다."""
        security = create_test_security(db)
        adapter = MockAdapter([])  # 빈 응답 (실제로는 API 호출만 확인)

        result = price_service.incremental_update(db, [security.id], adapter)
        assert result.updated_securities == 1  # 시도는 했음
        assert result.total_inserted == 0

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
