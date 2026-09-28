"""
3단계 테스트: price_quality (품질 검사 서비스)

MockAdapter로 거래일 조회를 대체하여 품질 검사 로직을 검증한다.
"""

from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models.schema import DailyPrice
from app.services.price_quality import PriceQualityService
from tests.test_stage3_price_service import MockAdapter, create_test_security

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
# 헬퍼                                                                  #
# ------------------------------------------------------------------ #

def insert_price(db, security_id: int, trade_date: date, **kwargs):
    price = DailyPrice(
        security_id=security_id,
        trade_date=trade_date,
        open=kwargs.get("open", 70000),
        high=kwargs.get("high", 72000),
        low=kwargs.get("low", 69000),
        close=kwargs.get("close", 71000),
        volume=kwargs.get("volume", 10_000_000),
        value=kwargs.get("value", 710_000_000_000),
        adjusted_close=kwargs.get("adjusted_close", 71000),
    )
    db.add(price)
    db.commit()
    return price


# ------------------------------------------------------------------ #
# 품질 검사 테스트                                                        #
# ------------------------------------------------------------------ #

class TestPriceQualityService:

    def test_no_issues_clean_data(self, db):
        """정상 데이터에서는 이슈가 발생하지 않는다."""
        security = create_test_security(db)
        adapter = MockAdapter([])
        svc = PriceQualityService(adapter=adapter)

        for i in range(5):
            insert_price(db, security.id, date(2024, 1, 2) + timedelta(days=i))

        report = svc.check_security(db, security.id)
        assert report.total_rows == 5
        assert len(report.negative_volumes) == 0
        assert len(report.adjusted_mismatches) == 0

    def test_detects_negative_volume(self, db):
        """음수 거래량이 있는 행을 감지한다."""
        security = create_test_security(db)
        adapter = MockAdapter([])
        svc = PriceQualityService(adapter=adapter)

        insert_price(db, security.id, date(2024, 1, 2), volume=-1)

        report = svc.check_security(db, security.id)
        assert len(report.negative_volumes) == 1
        assert report.negative_volumes[0].trade_date == date(2024, 1, 2)

    def test_detects_price_spike(self, db):
        """전일 대비 30% 초과 급변을 감지한다."""
        security = create_test_security(db)
        adapter = MockAdapter([])
        svc = PriceQualityService(spike_threshold=0.30, adapter=adapter)

        insert_price(db, security.id, date(2024, 1, 2), close=70000)
        insert_price(db, security.id, date(2024, 1, 3), close=100000)  # +42.8%

        report = svc.check_security(db, security.id)
        assert len(report.price_spikes) == 1
        assert report.price_spikes[0].trade_date == date(2024, 1, 3)

    def test_no_spike_within_threshold(self, db):
        """임계값 이내 변동은 급변으로 감지하지 않는다."""
        security = create_test_security(db)
        adapter = MockAdapter([])
        svc = PriceQualityService(spike_threshold=0.30, adapter=adapter)

        insert_price(db, security.id, date(2024, 1, 2), close=70000)
        insert_price(db, security.id, date(2024, 1, 3), close=79000)  # +12.8%

        report = svc.check_security(db, security.id)
        assert len(report.price_spikes) == 0

    def test_detects_adjusted_close_mismatch(self, db):
        """수정주가가 원시주가 대비 비정상 범위인 경우를 감지한다."""
        security = create_test_security(db)
        adapter = MockAdapter([])
        svc = PriceQualityService(
            adj_ratio_min=0.1, adj_ratio_max=2.0, adapter=adapter
        )

        # adjusted_close / close = 300000 / 70000 ≈ 4.28 → 최대 2.0 초과
        insert_price(db, security.id, date(2024, 1, 2), close=70000, adjusted_close=300000)

        report = svc.check_security(db, security.id)
        assert len(report.adjusted_mismatches) == 1

    def test_normal_adjusted_close_no_mismatch(self, db):
        """수정주가가 정상 범위이면 불일치로 감지하지 않는다."""
        security = create_test_security(db)
        adapter = MockAdapter([])
        svc = PriceQualityService(adapter=adapter)

        insert_price(db, security.id, date(2024, 1, 2), close=70000, adjusted_close=68000)

        report = svc.check_security(db, security.id)
        assert len(report.adjusted_mismatches) == 0

    def test_missing_date_detection(self, db):
        """실제 거래일 대비 결측 날짜를 감지한다."""
        security = create_test_security(db)

        # MockAdapter는 월~금을 거래일로 반환
        # 2024-01-02(화), 2024-01-03(수), 2024-01-04(목), 2024-01-05(금)
        # 2024-01-02만 입력 → 3건 결측
        adapter = MockAdapter([])
        svc = PriceQualityService(adapter=adapter)

        insert_price(db, security.id, date(2024, 1, 2))

        report = svc.check_security(
            db, security.id,
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 5),
        )
        # 총 4 거래일 중 1건 적재 → 3건 결측
        assert len(report.missing_dates) == 3

    def test_coverage_pct_calculation(self, db):
        """적재율(coverage_pct) 계산이 정확하다."""
        security = create_test_security(db)
        adapter = MockAdapter([])
        svc = PriceQualityService(adapter=adapter)

        # 4 거래일 중 2건 입력 → 50%
        insert_price(db, security.id, date(2024, 1, 2))
        insert_price(db, security.id, date(2024, 1, 3))

        report = svc.check_security(
            db, security.id,
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 5),
        )
        assert report.coverage_pct == pytest.approx(50.0, abs=1.0)

    def test_empty_data_returns_zero_report(self, db):
        """데이터가 없는 종목은 total_rows=0, coverage=0 리포트를 반환한다."""
        security = create_test_security(db)
        adapter = MockAdapter([])
        svc = PriceQualityService(adapter=adapter)

        report = svc.check_security(db, security.id)
        assert report.total_rows == 0
        assert report.coverage_pct == 0.0

    def test_empty_explicit_period_reports_all_dates_missing(self, db):
        security = create_test_security(db)
        svc = PriceQualityService(adapter=MockAdapter([]))
        report = svc.check_security(
            db, security.id, date(2024, 1, 2), date(2024, 1, 5)
        )
        assert report.expected_trading_days == 4
        assert report.coverage_pct == 0.0
        assert len(report.missing_dates) == 4

    def test_invalid_security_raises(self, db):
        """존재하지 않는 security_id는 ValueError를 발생시킨다."""
        adapter = MockAdapter([])
        svc = PriceQualityService(adapter=adapter)

        with pytest.raises(ValueError):
            svc.check_security(db, 9999)

    def test_to_dict_serializable(self, db):
        """to_dict() 결과가 직렬화 가능한 기본 타입으로 구성된다."""
        security = create_test_security(db)
        adapter = MockAdapter([])
        svc = PriceQualityService(adapter=adapter)

        insert_price(db, security.id, date(2024, 1, 2))
        report = svc.check_security(db, security.id)
        d = report.to_dict()

        assert isinstance(d["security_id"], int)
        assert isinstance(d["ticker"], str)
        assert isinstance(d["coverage_pct"], float)
        assert "issues" in d
