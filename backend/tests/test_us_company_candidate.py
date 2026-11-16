"""CORE-US-FLOW-03: Isolated unit tests for US Market/Industry signals -> US Company candidate resolution.

Verifies the 6 completion conditions:
1. Market signal source, symbol, and baseline timestamps are verified.
2. Relevant industries are identified via SYMBOL_INDUSTRY_EXPOSURE.
3. Evidence-grounded US company candidates are returned.
4. Differentiates industry exposure candidates from official SEC filing confirmed companies.
5. Confirmed SEC corporate events provide B4-forwardable input (sec_event_id, sec_anchor_link_id).
6. Ungrounded companies/Korean securities are not arbitrarily linked.
"""
from datetime import date, datetime, timezone
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models.schema import (
    Company,
    EventCompanyLink,
    GlobalEvent,
    GlobalEventEvidence,
    IdentifierMap,
    NewsArticle,
    Relationship,
    RelationshipEvidence,
    Security,
)
from app.services.event_supply_chain_service import EventSupplyChainService
from app.services.us_market_event_service import USMarketCompanyCandidateService

UTC = timezone.utc


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = Session(engine, autoflush=False)
    yield session
    session.close()
    engine.dispose()


def setup_us_flow_world(db: Session):
    """Sets up US companies in different evidence categories, Korean suppliers, and a market index event."""
    # 1. Market Event: PHLX Semiconductor Index (^SOX) Shock
    sox_event = GlobalEvent(
        id=1,
        external_id="yahoo:^SOX:2026-10-29:price_shock",
        source="YAHOO_FINANCE",
        origin_country="US",
        event_kind="MARKET_SHOCK",
        symbol="^SOX",
        title="PHLX Semiconductor Index positive shock",
        occurred_at=datetime(2026, 10, 29, 20, 0, 0),
        available_at=datetime(2026, 10, 30, 0, 0, 0),
        collected_at=datetime(2026, 10, 30, 2, 0, 0),
        return_1d=0.038,
        zscore_20d=2.85,
        shock_score=75.0,
        direction="POSITIVE",
        verification_status="proposed",
        raw_hash="hash-sox-event",
    )

    # 2. US Company 1: Micron Technology (Has official verified SEC 8-K Capex Event)
    micron = Company(
        id=100,
        name="Micron Technology",
        legal_name="Micron Technology, Inc.",
        country_code="US",
        industry_id="SEMICONDUCTORS_ELECTRONICS",
        verification_status="verified",
    )
    micron_cik = IdentifierMap(
        id=1,
        company_id=100,
        source="CIK",
        source_id_value="0000723125",
        effective_from=date(2010, 1, 1),
    )

    # SEC Event for Micron
    sec_event = GlobalEvent(
        id=10,
        external_id="sec:0000723125:0001104659-25-058741",
        source="SEC",
        origin_country="US",
        event_kind="CAPEX_EXPANSION",
        symbol="MU",
        title="Micron Technology Form 8-K Capex Expansion",
        occurred_at=datetime(2025, 6, 12, 12, 40, 40),
        available_at=datetime(2025, 6, 12, 12, 40, 40),
        collected_at=datetime(2026, 10, 30, 2, 22, 56),
        verification_status="verified",
        shock_score=0,
        raw_hash="hash-sec-event",
    )
    sec_ev = GlobalEventEvidence(
        id=10,
        event_id=10,
        evidence_type="REGULATORY",
        source_url="https://www.sec.gov/Archives/edgar/data/723125/000110465925058741/tm2517778d1_8k.htm",
        source_document="SEC Form 8-K 0001104659-25-058741",
        excerpt="Micron announced manufacturing expansion investment plan for new memory fabs.",
        published_at=datetime(2025, 6, 12, 12, 40, 40),
        collected_at=datetime(2026, 10, 30, 2, 22, 56),
        verification_status="verified",
        evidence_hash="hash-sec-ev",
    )
    sec_anchor = EventCompanyLink(
        id=10,
        event_id=10,
        company_id=100,
        role="ANCHOR",
        evidence_id=10,
        verification_status="verified",
        valid_from=date(2025, 1, 1),
    )

    # 3. US Company 2: Qualcomm (Has industry exposure, but NO SEC official event)
    qualcomm = Company(
        id=101,
        name="Qualcomm",
        legal_name="Qualcomm Incorporated",
        country_code="US",
        industry_id="SEMICONDUCTORS_ELECTRONICS",
        verification_status="verified",
    )
    qcom_cik = IdentifierMap(
        id=2,
        company_id=101,
        source="CIK",
        source_id_value="0000804328",
        effective_from=date(2010, 1, 1),
    )

    # 4. US Company 3: US Steel (Different industry: METALS, no exposure to ^SOX)
    us_steel = Company(
        id=102,
        name="United States Steel",
        legal_name="United States Steel Corporation",
        country_code="US",
        industry_id="METALS",
        verification_status="verified",
    )

    # 5. Korean Supplier to Micron (Hanmi Semiconductor)
    hanmi = Company(
        id=200,
        name="HANMI Semiconductor",
        legal_name="HANMI Semiconductor CO., LTD.",
        country_code="KR",
        verification_status="verified",
    )
    hanmi_sec = Security(
        id=200,
        company_id=200,
        market="KOSPI",
        ticker="042700",
        listed_at=date(2010, 1, 1),
        effective_from=date(2010, 1, 1),
    )
    hanmi_ticker = IdentifierMap(
        id=3,
        company_id=200,
        source="KRX_TICKER",
        source_id_value="042700",
        effective_from=date(2010, 1, 1),
    )
    rel = Relationship(
        id=1,
        source_id=200,
        target_id=100,
        type="SUPPLIES_TO",
        status="verified",
        confidence=0.95,
        valid_from=date(2024, 1, 1),
    )
    rel_ev = RelationshipEvidence(
        id=1,
        relationship_id=1,
        excerpt="Hanmi Semiconductor supplies equipment to Micron.",
        source_document="Supplier Recognition",
        published_at=datetime(2024, 8, 1, 10, 0, 0),
        first_seen_at=datetime(2024, 8, 1, 10, 0, 0),
        verification_status="verified",
        confidence=0.95,
        evidence_hash="hash-rel-ev-1",
    )

    db.add_all([
        sox_event,
        micron, micron_cik, sec_event, sec_ev, sec_anchor,
        qualcomm, qcom_cik,
        us_steel,
        hanmi, hanmi_sec, hanmi_ticker, rel, rel_ev,
    ])
    db.commit()


# -----------------------------------------------------------------------------
# Test 1 & 2: Market signal verification and industry identification
# -----------------------------------------------------------------------------
def test_market_signal_verification_and_industries(db_session):
    setup_us_flow_world(db_session)

    as_of = datetime(2026, 10, 30, 12, 0, 0, tzinfo=UTC)
    result = USMarketCompanyCandidateService.resolve_candidates(
        db_session,
        market_event_id=1,
        as_of=as_of,
    )

    # Condition 1: Market signal verified
    signal = result["market_signal"]
    assert signal["event_id"] == 1
    assert signal["source"] == "YAHOO_FINANCE"
    assert signal["symbol"] == "^SOX"
    assert signal["direction"] == "POSITIVE"
    assert signal["return_1d"] == 0.038
    assert signal["shock_score"] == 75.0
    assert signal["occurred_at"] == "2026-10-29T20:00:00"

    # Condition 2: Relevant industries identified
    industries = result["identified_industries"]
    assert "SEMICONDUCTORS_ELECTRONICS" in industries
    assert industries["SEMICONDUCTORS_ELECTRONICS"] == 1.00
    assert "ELECTRICAL_EQUIPMENT" in industries


# -----------------------------------------------------------------------------
# Test 3 & 4: Differentiation of SEC event candidate vs Market exposure candidate
# -----------------------------------------------------------------------------
def test_differentiate_sec_candidate_and_market_exposure(db_session):
    setup_us_flow_world(db_session)

    as_of = datetime(2026, 10, 30, 12, 0, 0, tzinfo=UTC)
    result = USMarketCompanyCandidateService.resolve_candidates(
        db_session,
        market_event_id=1,
        as_of=as_of,
    )

    candidates = result["us_company_candidates"]
    by_name = {c["company_name"]: c for c in candidates}

    # Condition 3 & 4: Micron has official SEC event
    assert "Micron Technology" in by_name
    m_cand = by_name["Micron Technology"]
    assert m_cand["candidate_category"] == "sec_event_candidate"
    assert m_cand["b4_routing_eligible"] is True
    assert m_cand["sec_event_id"] == 10
    assert m_cand["sec_external_id"] == "sec:0000723125:0001104659-25-058741"
    assert m_cand["sec_anchor_link_id"] == 10
    assert m_cand["cik"] == "0000723125"

    # Condition 3 & 4: Qualcomm only has industry exposure (no SEC event)
    assert "Qualcomm" in by_name
    q_cand = by_name["Qualcomm"]
    assert q_cand["candidate_category"] == "market_exposure_candidate"
    assert q_cand["b4_routing_eligible"] is False
    assert q_cand["sec_event_id"] is None
    assert q_cand["exposure_weight"] == 1.00
    assert q_cand["b4_ineligibility_reason"] == "no_official_sec_event_unverified_industry_exposure"

    # US Steel has no industry exposure to ^SOX and no SEC event -> Excluded from candidates
    assert "United States Steel" not in by_name


# -----------------------------------------------------------------------------
# Test 5: Forwarding confirmed SEC candidate to existing B4 supply chain engine
# -----------------------------------------------------------------------------
def test_forward_sec_candidate_to_b4_engine(db_session):
    setup_us_flow_world(db_session)

    as_of = datetime(2026, 10, 30, 12, 0, 0, tzinfo=UTC)
    result = USMarketCompanyCandidateService.resolve_candidates(
        db_session,
        market_event_id=1,
        as_of=as_of,
    )

    forwardable = result["b4_forwardable_candidates"]
    assert len(forwardable) == 1
    target = forwardable[0]
    assert target["company_name"] == "Micron Technology"

    # Forward to existing B4 EventSupplyChainService
    b4 = EventSupplyChainService()
    b4_res = b4.analyze(db_session, event_id=target["sec_event_id"], as_of=as_of)

    # Condition 5: B4 reaches Korean supplier (Hanmi Semiconductor)
    assert len(b4_res["candidates"]) == 1
    kr_cand = b4_res["candidates"][0]
    assert kr_cand["company_id"] == 200
    assert kr_cand["name"] == "HANMI Semiconductor"
    assert kr_cand["securities"][0]["ticker"] == "042700"
    assert kr_cand["paths"][0]["tier"] == 1
    assert kr_cand["paths"][0]["node_path"] == [200, 100]


# -----------------------------------------------------------------------------
# Test 6: Market exposure candidates cannot be arbitrarily forwarded to B4
# -----------------------------------------------------------------------------
def test_market_exposure_candidates_cannot_bypass_to_b4(db_session):
    setup_us_flow_world(db_session)

    as_of = datetime(2026, 10, 30, 12, 0, 0, tzinfo=UTC)
    result = USMarketCompanyCandidateService.resolve_candidates(
        db_session,
        market_event_id=1,
        as_of=as_of,
    )

    unforwardable = result["market_exposure_candidates"]
    assert len(unforwardable) == 1
    qcom = unforwardable[0]
    assert qcom["company_name"] == "Qualcomm"
    assert qcom["b4_routing_eligible"] is False
    assert qcom["sec_event_id"] is None

    # Condition 6: No arbitrary connection of ungrounded domestic Korean stocks
    # Since b4_routing_eligible is False, caller knows not to run B4 without a corporate anchor

