"""CORE-US-FLOW-02: Isolated unit tests for SEC official event -> US Company Anchor binding.

Verifies the 7 completion conditions:
1. SEC CIK maps uniquely to a verified US company.
2. SEC official event and regulatory evidence are linked.
3. Event and US company anchor are created/retrieved.
4. All newly created validation statuses remain 'proposed'.
5. Proposed (unapproved) anchors/events are excluded by B4.
6. Verified events/anchors connect seamlessly to B4/B3 Korean candidates.
7. Idempotent ingestion prevents duplicate events, evidences, and anchor links.
8. Conflict / missing identifier safely holds anchor creation without error.
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
    Relationship,
    RelationshipEvidence,
    Security,
)
from app.services.event_evidence_service import SecEventAnchorService
from app.services.event_supply_chain_service import EventSupplyChainService

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


def setup_micron_hanmi_world(db: Session):
    """Sets up standard verified US Micron and Korean Hanmi Semiconductor entities."""
    # US Company Micron
    micron = Company(
        id=100,
        name="Micron Technology",
        legal_name="Micron Technology, Inc.",
        country_code="US",
        verification_status="verified",
    )
    # Korean Company Hanmi Semiconductor
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
    micron_cik = IdentifierMap(
        id=1,
        company_id=100,
        source="CIK",
        source_id_value="0000723125",
        effective_from=date(2010, 1, 1),
    )
    hanmi_ticker = IdentifierMap(
        id=2,
        company_id=200,
        source="KRX_TICKER",
        source_id_value="042700",
        effective_from=date(2010, 1, 1),
    )

    # Verified Relationship between Hanmi and Micron
    rel = Relationship(
        id=1,
        source_id=200, # Supplier
        target_id=100, # Customer
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
        evidence_hash="rel-ev-hash-1",
    )
    db.add_all([micron, hanmi, hanmi_sec, micron_cik, hanmi_ticker, rel, rel_ev])
    db.commit()


# -----------------------------------------------------------------------------
# Test 1, 2, 3, 4: Ingest SEC filing creates proposed event, evidence, anchor
# -----------------------------------------------------------------------------
def test_ingest_sec_filing_links_anchor_as_proposed(db_session):
    setup_micron_hanmi_world(db_session)

    res = SecEventAnchorService.ingest_sec_filing(
        db_session,
        cik="0000723125",
        accession_number="0001104659-25-058741",
        title="Micron Technology Form 8-K Capex Expansion",
        published_at=datetime(2025, 6, 12, 12, 40, 40, tzinfo=UTC),
        collected_at=datetime(2026, 10, 30, 2, 22, 56, tzinfo=UTC),
        source_url="https://www.sec.gov/Archives/edgar/data/723125/000110465925058741/tm2517778d1_8k.htm",
        summary="Micron announced manufacturing expansion investment plan.",
        event_kind="CAPEX_EXPANSION",
        symbol="MU",
    )
    db_session.commit()

    # 1. Successful link status
    assert res["status"] == "linked_proposed"
    assert res["company_id"] == 100
    assert res["created_event"] is True
    assert res["created_evidence"] is True

    # 2. Check GlobalEvent
    event = db_session.get(GlobalEvent, res["event_id"])
    assert event is not None
    assert event.external_id == "sec:0000723125:0001104659-25-058741"
    assert event.source == "SEC"
    assert event.origin_country == "US"
    assert event.verification_status == "proposed"  # Condition 4: proposed!

    # 3. Check GlobalEventEvidence
    evidence = db_session.get(GlobalEventEvidence, res["evidence_id"])
    assert evidence is not None
    assert evidence.event_id == event.id
    assert evidence.evidence_type == "REGULATORY"
    assert evidence.verification_status == "proposed"  # Condition 4: proposed!

    # 4. Check EventCompanyLink
    anchor_link = db_session.get(EventCompanyLink, res["anchor_link_id"])
    assert anchor_link is not None
    assert anchor_link.event_id == event.id
    assert anchor_link.company_id == 100
    assert anchor_link.role == "ANCHOR"
    assert anchor_link.verification_status == "proposed"  # Condition 4: proposed!


# -----------------------------------------------------------------------------
# Test 5: Proposed anchor / event is excluded from B4 traversal
# -----------------------------------------------------------------------------
def test_unverified_sec_event_excluded_from_b4(db_session):
    setup_micron_hanmi_world(db_session)

    res = SecEventAnchorService.ingest_sec_filing(
        db_session,
        cik="0000723125",
        accession_number="0001104659-25-058741",
        title="Micron Technology Form 8-K Capex Expansion",
        published_at=datetime(2025, 6, 12, 12, 40, 40, tzinfo=UTC),
        collected_at=datetime(2026, 10, 30, 2, 22, 56, tzinfo=UTC),
        source_url="https://www.sec.gov/Archives/edgar/data/723125/000110465925058741/tm2517778d1_8k.htm",
        summary="Micron announced manufacturing expansion investment plan.",
    )
    db_session.commit()

    # B4 analysis
    b4 = EventSupplyChainService()
    as_of = datetime(2026, 10, 30, 12, 0, 0, tzinfo=UTC)
    result = b4.analyze(db_session, event_id=res["event_id"], as_of=as_of)

    # Condition 5: Unverified event/anchor must be excluded from B4
    assert result["candidates"] == []
    assert "event_not_verified" in result["empty_reasons"]


# -----------------------------------------------------------------------------
# Test 6: Verified event / anchor connects to B4 Korean candidate
# -----------------------------------------------------------------------------
def test_verified_sec_event_reaches_korean_candidate_via_b4(db_session):
    setup_micron_hanmi_world(db_session)

    res = SecEventAnchorService.ingest_sec_filing(
        db_session,
        cik="0000723125",
        accession_number="0001104659-25-058741",
        title="Micron Technology Form 8-K Capex Expansion",
        published_at=datetime(2025, 6, 12, 12, 40, 40, tzinfo=UTC),
        collected_at=datetime(2026, 10, 30, 2, 22, 56, tzinfo=UTC),
        source_url="https://www.sec.gov/Archives/edgar/data/723125/000110465925058741/tm2517778d1_8k.htm",
        summary="Micron announced manufacturing expansion investment plan.",
    )
    
    # Explicitly promote event, evidence, anchor to verified (e.g. after authorized review)
    event = db_session.get(GlobalEvent, res["event_id"])
    evidence = db_session.get(GlobalEventEvidence, res["evidence_id"])
    anchor_link = db_session.get(EventCompanyLink, res["anchor_link_id"])

    event.verification_status = "verified"
    evidence.verification_status = "verified"
    anchor_link.verification_status = "verified"
    db_session.commit()

    # B4 analysis
    b4 = EventSupplyChainService()
    as_of = datetime(2026, 10, 30, 12, 0, 0, tzinfo=UTC)
    result = b4.analyze(db_session, event_id=res["event_id"], as_of=as_of)

    # Condition 6: Verified anchor reaches Korean Hanmi Semiconductor (042700, KOSPI)
    assert len(result["candidates"]) == 1
    cand = result["candidates"][0]
    assert cand["company_id"] == 200
    assert cand["name"] == "HANMI Semiconductor"
    assert cand["securities"][0]["ticker"] == "042700"
    assert cand["securities"][0]["market"] == "KOSPI"
    assert cand["paths"][0]["tier"] == 1
    assert cand["paths"][0]["node_path"] == [200, 100]


# -----------------------------------------------------------------------------
# Test 7: Idempotency (no duplicate event, evidence, or anchor)
# -----------------------------------------------------------------------------
def test_idempotent_ingestion_no_duplicates(db_session):
    setup_micron_hanmi_world(db_session)

    kwargs = dict(
        cik="0000723125",
        accession_number="0001104659-25-058741",
        title="Micron Technology Form 8-K Capex Expansion",
        published_at=datetime(2025, 6, 12, 12, 40, 40, tzinfo=UTC),
        collected_at=datetime(2026, 10, 30, 2, 22, 56, tzinfo=UTC),
        source_url="https://www.sec.gov/Archives/edgar/data/723125/000110465925058741/tm2517778d1_8k.htm",
        summary="Micron announced manufacturing expansion investment plan.",
    )

    first = SecEventAnchorService.ingest_sec_filing(db_session, **kwargs)
    db_session.commit()
    assert first["created_event"] is True
    assert first["created_evidence"] is True

    second = SecEventAnchorService.ingest_sec_filing(db_session, **kwargs)
    db_session.commit()

    # Condition 7: No duplicate created
    assert second["created_event"] is False
    assert second["created_evidence"] is False
    assert second["event_id"] == first["event_id"]
    assert second["evidence_id"] == first["evidence_id"]
    assert second["anchor_link_id"] == first["anchor_link_id"]

    assert db_session.query(GlobalEvent).count() == 1
    assert db_session.query(GlobalEventEvidence).count() == 1
    assert db_session.query(EventCompanyLink).count() == 1


# -----------------------------------------------------------------------------
# Test 8: Missing CIK mapping holds anchor creation
# -----------------------------------------------------------------------------
def test_missing_cik_mapping_holds_anchor_creation(db_session):
    setup_micron_hanmi_world(db_session)

    # Ingest with unknown CIK (9999999999)
    res = SecEventAnchorService.ingest_sec_filing(
        db_session,
        cik="9999999999",
        accession_number="0001104659-25-999999",
        title="Unknown Corp 8-K",
        published_at=datetime(2025, 6, 12, 12, 40, 40, tzinfo=UTC),
        collected_at=datetime(2026, 10, 30, 2, 22, 56, tzinfo=UTC),
        source_url="https://www.sec.gov/Archives/edgar/data/9999999999/unknown.htm",
    )
    db_session.commit()

    assert res["status"] == "hold_no_identifier_mapping"
    assert res["anchor_link_id"] is None
    assert db_session.query(EventCompanyLink).count() == 0


# -----------------------------------------------------------------------------
# Test 9: CIK conflict (multiple companies for same CIK) holds anchor creation
# -----------------------------------------------------------------------------
def test_cik_conflict_holds_anchor_creation(db_session):
    setup_micron_hanmi_world(db_session)

    # Add a second company with same CIK
    c_conflict = Company(id=300, name="Conflicting Corp", country_code="US", verification_status="verified")
    map_conflict = IdentifierMap(id=3, company_id=300, source="CIK", source_id_value="0000723125")
    db_session.add_all([c_conflict, map_conflict])
    db_session.commit()

    res = SecEventAnchorService.ingest_sec_filing(
        db_session,
        cik="0000723125",
        accession_number="0001104659-25-058741",
        title="Micron 8-K with CIK conflict",
        published_at=datetime(2025, 6, 12, 12, 40, 40, tzinfo=UTC),
        collected_at=datetime(2026, 10, 30, 2, 22, 56, tzinfo=UTC),
        source_url="https://www.sec.gov/Archives/edgar/data/723125/conflict.htm",
    )
    db_session.commit()

    assert res["status"] == "hold_identifier_conflict"
    assert res["anchor_link_id"] is None
    assert db_session.query(EventCompanyLink).count() == 0
