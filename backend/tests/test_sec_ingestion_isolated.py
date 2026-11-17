"""CORE-EVIDENCE-02 Phase 2: Isolated unit tests for SEC filing ingestion.

Verifies:
1. SEC EDGAR adapter headers, fair-access User-Agent, and submissions parsing.
2. Rate-limit (429) backoff, 404 not found, and error hierarchy.
3. Payload validation (missing fields, invalid domain, future timestamp rejection).
4. Dry-run execution: verifies linking without persisting any DB rows.
5. Real persistent ingestion: creates GlobalEvent, GlobalEventEvidence, EventCompanyLink
   strictly in 'proposed' verification status.
6. Idempotency: duplicate submissions do not create duplicate DB rows.
7. Hold conditions: hold_no_identifier_mapping, hold_not_us_company.
8. End-to-end ingest_latest_filing_by_cik with mock SEC adapter.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.adapters.sec_adapter import (
    SecAuthError,
    SecEdgarAdapter,
    SecNotFoundError,
)
from app.db.session import Base
from app.models.schema import (
    Company,
    EventCompanyLink,
    GlobalEvent,
    GlobalEventEvidence,
    IdentifierMap,
)
from app.services.event_supply_chain_service import EventSupplyChainService
from app.services.sec_filing_service import SecFilingService, SecFilingValidationError

UTC = timezone.utc


@pytest.fixture
def db_session():
    """Isolated in-memory SQLite test database."""
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


def setup_us_company_world(db: Session) -> dict[str, Any]:
    """Create verified US Micron company and CIK IdentifierMap."""
    micron = Company(
        id=100,
        name="Micron Technology",
        legal_name="Micron Technology, Inc.",
        country_code="US",
        verification_status="verified",
    )
    db.add(micron)

    # Active CIK mapping for Micron (CIK: 0000723125)
    cik_map = IdentifierMap(
        id=1,
        company_id=100,
        source="CIK",
        source_id_value="0000723125",
        effective_from=datetime(2000, 1, 1, tzinfo=UTC),
        effective_to=None,
    )
    db.add(cik_map)

    # Korean company for testing non-US hold
    korean_co = Company(
        id=200,
        name="Korean Semiconductor Co",
        legal_name="Korean Semiconductor Co., Ltd.",
        country_code="KR",
        verification_status="verified",
    )
    db.add(korean_co)
    kr_cik_map = IdentifierMap(
        id=2,
        company_id=200,
        source="CIK",
        source_id_value="0000999999",
        effective_from=datetime(2000, 1, 1, tzinfo=UTC),
        effective_to=None,
    )
    db.add(kr_cik_map)

    db.commit()
    return {"micron_id": 100, "korean_id": 200}


SAMPLE_SUBMISSIONS_PAYLOAD = {
    "cik": "723125",
    "entityType": "operating",
    "name": "MICRON TECHNOLOGY INC",
    "tickers": ["MU"],
    "exchanges": ["Nasdaq"],
    "filings": {
        "recent": {
            "accessionNumber": ["0001104659-25-058741", "0001104659-25-000001"],
            "filingDate": ["2025-06-12", "2025-05-01"],
            "reportDate": ["2025-06-12", "2025-05-01"],
            "acceptanceDateTime": ["2025-06-12T12:40:40.000Z", "2025-05-01T08:00:00.000Z"],
            "form": ["8-K", "10-Q"],
            "primaryDocument": ["tm2517778d1_8k.htm", "tm2500000d1_10q.htm"],
            "primaryDocDescription": ["Current report Form 8-K", "Quarterly report Form 10-Q"],
            "items": ["8.01", ""],
        }
    },
}


# =============================================================================
# 1. SecEdgarAdapter Tests
# =============================================================================


def test_sec_adapter_headers_and_parsing():
    """Verify SEC fair-access User-Agent header and recent filings parsing."""
    recorded_headers = {}

    def handler(request: httpx.Request) -> httpx.Response:
        recorded_headers.update(dict(request.headers))
        assert request.url.path == "/submissions/CIK0000723125.json"
        return httpx.Response(200, json=SAMPLE_SUBMISSIONS_PAYLOAD)

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)

    with SecEdgarAdapter(user_agent="AlphaChainBot/1.0 (test@alphachain.org)", client=client) as adapter:
        filings = adapter.get_recent_filings("723125", form_types={"8-K"})
        assert len(filings) == 1
        f = filings[0]
        assert f["cik"] == "0000723125"
        assert f["accession_number"] == "0001104659-25-058741"
        assert f["form"] == "8-K"
        assert f["source_url"] == "https://www.sec.gov/Archives/edgar/data/723125/000110465925058741/tm2517778d1_8k.htm"
        assert f["published_at"] == datetime(2025, 6, 12, 12, 40, 40, tzinfo=UTC)
        assert f["symbol"] == "MU"

    assert recorded_headers.get("user-agent") == "AlphaChainBot/1.0 (test@alphachain.org)"


def test_sec_adapter_rate_limit_and_retry():
    """Verify exponential backoff on HTTP 429."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(429, headers={"Retry-After": "0.01"})
        return httpx.Response(200, json=SAMPLE_SUBMISSIONS_PAYLOAD)

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)

    adapter = SecEdgarAdapter(client=client, rate_limit_per_sec=100.0)
    data = adapter.get_submissions("723125")
    assert call_count == 2
    assert data["name"] == "MICRON TECHNOLOGY INC"


def test_sec_adapter_404_not_found():
    """Verify SecNotFoundError on missing CIK."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)

    adapter = SecEdgarAdapter(client=client)
    with pytest.raises(SecNotFoundError):
        adapter.get_submissions("0000000000")


def test_sec_adapter_403_auth_error():
    """Verify SecAuthError on rejected User-Agent."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403)

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)

    adapter = SecEdgarAdapter(client=client)
    with pytest.raises(SecAuthError):
        adapter.get_submissions("723125")


# =============================================================================
# 2. SecFilingService Validation Tests
# =============================================================================


def test_sec_filing_service_payload_validation():
    """Verify validation rules on missing fields, future dates, and URL domain."""
    # 1. Missing CIK
    with pytest.raises(SecFilingValidationError, match="cik is required"):
        SecFilingService.validate_filing_payload({"accession_number": "123"})

    # 2. Missing accession number
    with pytest.raises(SecFilingValidationError, match="accession_number is required"):
        SecFilingService.validate_filing_payload({"cik": "723125"})

    # 3. Invalid source URL domain
    with pytest.raises(SecFilingValidationError, match="Invalid official SEC source_url domain"):
        SecFilingService.validate_filing_payload({
            "cik": "723125",
            "accession_number": "123",
            "source_url": "https://malicious.com/fake.htm",
            "published_at": datetime(2025, 1, 1, tzinfo=UTC),
        })

    # 4. Future published_at rejected
    future_dt = datetime.now(UTC) + timedelta(days=10)
    with pytest.raises(SecFilingValidationError, match="published_at cannot be in the future"):
        SecFilingService.validate_filing_payload({
            "cik": "723125",
            "accession_number": "123",
            "source_url": "https://www.sec.gov/Archives/edgar/data/723125/123/doc.htm",
            "published_at": future_dt,
        })


# =============================================================================
# 3. Dry-Run Ingestion Tests
# =============================================================================


def test_sec_filing_service_dry_run_no_writes(db_session: Session):
    """Verify dry_run=True previews status without persisting any rows to DB."""
    setup_us_company_world(db_session)

    filing = {
        "cik": "723125",
        "accession_number": "0001104659-25-058741",
        "title": "Micron SEC Form 8-K",
        "published_at": datetime(2025, 6, 12, 12, 40, 40, tzinfo=UTC),
        "source_url": "https://www.sec.gov/Archives/edgar/data/723125/000110465925058741/tm2517778d1_8k.htm",
        "summary": "Expansion of high-bandwidth memory packaging",
    }

    res = SecFilingService.ingest_filing(db_session, filing_data=filing, dry_run=True)

    assert res["dry_run"] is True
    assert res["persisted"] is False
    assert res["status"] == "linked_proposed"
    assert res["company_id"] == 100

    # Ensure no rows were persisted
    assert db_session.query(GlobalEvent).count() == 0
    assert db_session.query(GlobalEventEvidence).count() == 0
    assert db_session.query(EventCompanyLink).count() == 0


# =============================================================================
# 4. Real Persistent Ingestion Tests
# =============================================================================


def test_sec_filing_service_real_ingest_proposed(db_session: Session):
    """Verify real persistent ingestion creates GlobalEvent, Evidence, Anchor in 'proposed'."""
    setup_us_company_world(db_session)

    filing = {
        "cik": "723125",
        "accession_number": "0001104659-25-058741",
        "title": "Micron SEC Form 8-K",
        "published_at": datetime(2025, 6, 12, 12, 40, 40, tzinfo=UTC),
        "source_url": "https://www.sec.gov/Archives/edgar/data/723125/000110465925058741/tm2517778d1_8k.htm",
        "summary": "Expansion of high-bandwidth memory packaging",
        "symbol": "MU",
    }

    res = SecFilingService.ingest_filing(db_session, filing_data=filing, dry_run=False)

    assert res["dry_run"] is False
    assert res["persisted"] is True
    assert res["status"] == "linked_proposed"
    assert res["verification_status"] == "proposed"

    # Verify DB records
    event = db_session.get(GlobalEvent, res["event_id"])
    assert event is not None
    assert event.source == "SEC"
    assert event.origin_country == "US"
    assert event.verification_status == "proposed"
    assert event.shock_score == 0

    evidence = db_session.get(GlobalEventEvidence, res["evidence_id"])
    assert evidence is not None
    assert evidence.evidence_type == "REGULATORY"
    assert evidence.verification_status == "proposed"

    anchor = db_session.get(EventCompanyLink, res["anchor_link_id"])
    assert anchor is not None
    assert anchor.role == "ANCHOR"
    assert anchor.company_id == 100
    assert anchor.verification_status == "proposed"

    # Strict supply chain verification: unapproved anchors MUST NOT be traversed by B4
    b4 = EventSupplyChainService()
    as_of = datetime(2026, 10, 30, 12, 0, 0, tzinfo=UTC)
    b4_result = b4.analyze(db_session, event_id=event.id, as_of=as_of)
    assert b4_result["candidates"] == []
    assert "event_not_verified" in b4_result["empty_reasons"]


def test_sec_filing_service_idempotent_duplicate_calls(db_session: Session):
    """Verify re-ingesting identical SEC filing is idempotent (no duplicate rows)."""
    setup_us_company_world(db_session)

    filing = {
        "cik": "723125",
        "accession_number": "0001104659-25-058741",
        "title": "Micron SEC Form 8-K",
        "published_at": datetime(2025, 6, 12, 12, 40, 40, tzinfo=UTC),
        "source_url": "https://www.sec.gov/Archives/edgar/data/723125/000110465925058741/tm2517778d1_8k.htm",
        "summary": "Expansion of high-bandwidth memory packaging",
    }

    first = SecFilingService.ingest_filing(db_session, filing_data=filing, dry_run=False)
    second = SecFilingService.ingest_filing(db_session, filing_data=filing, dry_run=False)

    assert first["event_id"] == second["event_id"]
    assert first["evidence_id"] == second["evidence_id"]
    assert first["anchor_link_id"] == second["anchor_link_id"]

    assert db_session.query(GlobalEvent).count() == 1
    assert db_session.query(GlobalEventEvidence).count() == 1
    assert db_session.query(EventCompanyLink).count() == 1


# =============================================================================
# 5. Hold Conditions Tests
# =============================================================================


def test_sec_filing_service_hold_no_identifier_mapping(db_session: Session):
    """Verify hold_no_identifier_mapping when CIK has no active IdentifierMap."""
    setup_us_company_world(db_session)

    filing = {
        "cik": "0000888888",  # Unmapped CIK
        "accession_number": "0000888888-25-000001",
        "title": "Unmapped Entity SEC Form 8-K",
        "published_at": datetime(2025, 6, 12, 12, 40, 40, tzinfo=UTC),
        "source_url": "https://www.sec.gov/Archives/edgar/data/888888/000088888825000001/doc.htm",
    }

    res = SecFilingService.ingest_filing(db_session, filing_data=filing, dry_run=False)

    assert res["status"] == "hold_no_identifier_mapping"
    assert res["anchor_link_id"] is None
    assert "No active IdentifierMap found" in res["reason"]

    # GlobalEvent and Evidence are saved for review/audit, but no Anchor link
    assert db_session.query(GlobalEvent).count() == 1
    assert db_session.query(GlobalEventEvidence).count() == 1
    assert db_session.query(EventCompanyLink).count() == 0


def test_sec_filing_service_hold_not_us_company(db_session: Session):
    """Verify hold_not_us_company when mapped company country_code != 'US'."""
    setup_us_company_world(db_session)

    filing = {
        "cik": "0000999999",  # Maps to company_id=200 (KR)
        "accession_number": "0000999999-25-000001",
        "title": "Korean Co SEC Form 6-K",
        "published_at": datetime(2025, 6, 12, 12, 40, 40, tzinfo=UTC),
        "source_url": "https://www.sec.gov/Archives/edgar/data/999999/000099999925000001/doc.htm",
    }

    res = SecFilingService.ingest_filing(db_session, filing_data=filing, dry_run=False)

    assert res["status"] == "hold_not_us_company"
    assert res["anchor_link_id"] is None
    assert "not a verified US company" in res["reason"]

    assert db_session.query(EventCompanyLink).count() == 0


# =============================================================================
# 6. End-to-End ingest_latest_filing_by_cik with Mock Adapter
# =============================================================================


def test_sec_filing_service_ingest_latest_by_cik(db_session: Session):
    """Verify end-to-end flow from CIK lookup through adapter to proposed anchor link."""
    setup_us_company_world(db_session)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=SAMPLE_SUBMISSIONS_PAYLOAD)

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    mock_adapter = SecEdgarAdapter(client=client)

    res = SecFilingService.ingest_latest_filing_by_cik(
        db_session,
        cik="723125",
        form_type="8-K",
        dry_run=False,
        adapter=mock_adapter,
        event_kind="CAPEX_EXPANSION",
    )

    assert res["status"] == "linked_proposed"
    assert res["company_id"] == 100
    assert res["verification_status"] == "proposed"

    event = db_session.get(GlobalEvent, res["event_id"])
    assert event.event_kind == "CAPEX_EXPANSION"
    assert event.symbol == "MU"
