"""Isolated test for CORE-MARKET-REACTION-01:
Validates market reaction integration for US Event -> Korean Security (Hanmi Semiconductor 042700).
Checks Korean market close (15:30 KST) timing rule, pre/post reaction prices, insufficient data handling,
and non-causal disclaimer.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models.schema import Company, DailyPrice, GlobalEvent, ScoreSnapshot, Security
from app.services.event_first_query_service import EventFirstQueryService


@pytest.fixture
def mem_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = Session(engine, autoflush=False)
    yield session
    session.rollback()
    session.close()
    engine.dispose()


def test_post_market_event_first_reaction_next_day(mem_db):
    """
    US SEC 8-K filed at 2025-06-12 12:40:40 UTC (21:40:40 KST).
    Since 21:40 >= 15:30 (KRX close), the reaction timing must be 'next_trading_day':
    - Base trade date: 2025-06-12
    - Reaction trade date: 2025-06-13
    """
    comp = Company(id=1, name="한미반도체", corp_code="00159074")
    sec = Security(
        id=427,
        company_id=1,
        ticker="042700",
        market="KOSPI",
    )
    mem_db.add_all([comp, sec])

    # 2025-06-12 base price
    p_base = DailyPrice(
        security_id=427,
        trade_date=date(2025, 6, 12),
        open=Decimal("150000"),
        high=Decimal("155000"),
        low=Decimal("148000"),
        close=Decimal("152000"),
        adjusted_close=Decimal("152000"),
        volume=1000000,
    )
    # 2025-06-13 reaction price
    p_rx = DailyPrice(
        security_id=427,
        trade_date=date(2025, 6, 13),
        open=Decimal("156000"),
        high=Decimal("165000"),
        low=Decimal("155000"),
        close=Decimal("162000"),
        adjusted_close=Decimal("162000"),
        volume=2500000,
    )
    mem_db.add_all([p_base, p_rx])
    mem_db.commit()

    event_time = datetime(2025, 6, 12, 12, 40, 40, tzinfo=timezone.utc)
    as_of = datetime(2025, 6, 13, 23, 59, 59, tzinfo=timezone.utc)

    result = EventFirstQueryService._market_reaction(
        mem_db,
        security_id=427,
        event_time=event_time,
        as_of=as_of,
    )

    assert result["status"] == "available"
    assert result["reaction_timing"] == "next_trading_day"
    assert result["base_trade_date"] == "2025-06-12"
    assert result["base_price"] == 152000.0
    assert result["base_volume"] == 1000000
    assert result["reaction_trade_date"] == "2025-06-13"
    assert result["reaction_price"] == 162000.0
    assert result["reaction_volume"] == 2500000
    # (162000 - 152000) / 152000 * 100 = 6.58%
    assert result["return_pct"] == 6.58
    # (2500000 - 1000000) / 1000000 * 100 = 150.0%
    assert result["volume_change_pct"] == 150.0
    assert "인과관계를 입증하지 않습니다" in result["causality_note"]


def test_pre_market_event_first_reaction_same_day(mem_db):
    """
    Event occurred at 2025-06-12 02:00:00 UTC (11:00:00 KST, during trading hours).
    Reaction timing must be 'same_trading_day':
    - Base trade date: 2025-06-11
    - Reaction trade date: 2025-06-12
    """
    comp = Company(id=1, name="한미반도체", corp_code="00159074")
    sec = Security(id=427, company_id=1, ticker="042700", market="KOSPI")
    mem_db.add_all([comp, sec])

    p_prior = DailyPrice(
        security_id=427,
        trade_date=date(2025, 6, 11),
        close=Decimal("150000"),
        volume=800000,
    )
    p_current = DailyPrice(
        security_id=427,
        trade_date=date(2025, 6, 12),
        close=Decimal("153000"),
        volume=1200000,
    )
    mem_db.add_all([p_prior, p_current])
    mem_db.commit()

    event_time = datetime(2025, 6, 12, 2, 0, 0, tzinfo=timezone.utc)
    as_of = datetime(2025, 6, 12, 23, 59, 59, tzinfo=timezone.utc)

    result = EventFirstQueryService._market_reaction(
        mem_db,
        security_id=427,
        event_time=event_time,
        as_of=as_of,
    )

    assert result["status"] == "available"
    assert result["reaction_timing"] == "same_trading_day"
    assert result["base_trade_date"] == "2025-06-11"
    assert result["reaction_trade_date"] == "2025-06-12"
    assert result["return_pct"] == 2.0
    assert result["volume_change_pct"] == 50.0


def test_missing_reaction_price_yields_insufficient_market_data_never_zero(mem_db):
    """
    If reaction day price data is missing (e.g. holiday or not yet collected),
    status must be 'insufficient_market_data', NEVER substituted with 0.
    """
    comp = Company(id=1, name="한미반도체", corp_code="00159074")
    sec = Security(id=427, company_id=1, ticker="042700", market="KOSPI")
    mem_db.add_all([comp, sec])

    # Only base price exists, no reaction price
    p_base = DailyPrice(
        security_id=427,
        trade_date=date(2025, 6, 12),
        close=Decimal("152000"),
        volume=1000000,
    )
    mem_db.add(p_base)
    mem_db.commit()

    event_time = datetime(2025, 6, 12, 12, 40, 40, tzinfo=timezone.utc)
    as_of = datetime(2025, 6, 15, 0, 0, 0, tzinfo=timezone.utc)

    result = EventFirstQueryService._market_reaction(
        mem_db,
        security_id=427,
        event_time=event_time,
        as_of=as_of,
    )

    assert result["status"] == "insufficient_market_data"
    assert result["reaction_trade_date"] is None
    assert "return_pct" not in result
    assert "데이터가 충분하지 않아" in result["causality_note"]


def test_no_security_id_returns_not_available(mem_db):
    result = EventFirstQueryService._market_reaction(
        mem_db,
        security_id=None,
        event_time=datetime(2025, 6, 12, 12, 0, 0, tzinfo=timezone.utc),
        as_of=datetime(2025, 6, 13, 0, 0, 0, tzinfo=timezone.utc),
    )
    assert result["status"] == "not_available"


def test_candidate_integration_with_score_snapshot(mem_db):
    """
    Validate that _candidate() embeds both price, alpha_score, and market_reaction.
    """
    service = EventFirstQueryService()
    sec_id = 427
    comp = Company(id=10, name="한미반도체", corp_code="00159074")
    sec = Security(id=sec_id, company_id=10, ticker="042700", market="KOSPI")
    mem_db.add_all([
        comp,
        sec,
        DailyPrice(security_id=sec_id, trade_date=date(2025, 6, 12), close=Decimal("152000"), volume=1000000),
        DailyPrice(security_id=sec_id, trade_date=date(2025, 6, 13), close=Decimal("160000"), volume=2000000),
        ScoreSnapshot(
            security_id=sec_id,
            as_of_date=date(2025, 6, 13),
            horizon="20d",
            version="v1.0",
            total=84.5,
            confidence=0.92,
            completeness=0.88,
            component={"supply_chain": 0.85, "momentum": 0.80},
        )
    ])
    mem_db.commit()

    candidate_row = {
        "company_id": 10,
        "name": "한미반도체",
        "securities": [{"security_id": sec_id, "ticker": "042700", "market": "KOSPI"}],
        "paths": [{"tier": 1, "relationships": []}],
    }

    event_time = datetime(2025, 6, 12, 12, 40, 40, tzinfo=timezone.utc)
    as_of = datetime(2025, 6, 13, 23, 59, 59, tzinfo=timezone.utc)

    candidate = service._candidate(
        mem_db,
        row=candidate_row,
        as_of=as_of,
        horizon="20d",
        version="v1.0",
        event_time=event_time,
    )

    # 1. security extraction
    assert candidate["security_id"] == 427
    assert candidate["ticker"] == "042700"
    assert candidate["company_name"] == "한미반도체"

    # 2. price
    assert candidate["price"]["status"] == "available"
    assert candidate["price"]["close"] == 160000.0

    # 3. alpha_score
    assert candidate["alpha_score"]["status"] == "available"
    assert candidate["alpha_score"]["score"] == 84.5
    assert candidate["alpha_score"]["horizon"] == "20d"

    # 4. market_reaction
    assert candidate["market_reaction"]["status"] == "available"
    assert candidate["market_reaction"]["reaction_timing"] == "next_trading_day"
    assert candidate["market_reaction"]["base_trade_date"] == "2025-06-12"
    assert candidate["market_reaction"]["reaction_trade_date"] == "2025-06-13"
    assert candidate["market_reaction"]["base_price"] == 152000.0
    assert candidate["market_reaction"]["reaction_price"] == 160000.0
    assert candidate["market_reaction"]["return_pct"] == 5.26
    assert candidate["market_reaction"]["volume_change_pct"] == 100.0
    assert "인과관계를 입증하지 않습니다" in candidate["market_reaction"]["causality_note"]
