"""S2-B3: Point-in-time and availability tests for RelationshipEvidence.

Tests the 5 core time-integrity rules without running full regressions:
1. published_at is None + first_seen_at exists: used only on or after first_seen_at
2. Both published_at and first_seen_at exist: max(published_at, first_seen_at) is used
3. Both timestamps are None: evidence excluded
4. Unverified evidence: excluded even if timestamps are valid
5. Existing records with only published_at (first_seen_at is None): 100% backward compatible
"""
from datetime import date, datetime, timezone
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models.schema import Company, Relationship, RelationshipEvidence
from app.services.supply_chain_service import (
    SupplyChainService,
    get_evidence_available_at,
    to_utc_datetime,
)

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


def make_companies(db: Session):
    c1 = Company(id=10, name="Supplier Corp", country_code="KR", verification_status="verified")
    c2 = Company(id=20, name="Anchor Corp", country_code="US", verification_status="verified")
    db.add_all([c1, c2])
    db.commit()
    return c1, c2


# -----------------------------------------------------------------------------
# Test 1: 공개시각 미상 + 최초 확인시각 존재 -> 확인시각 이후에만 사용
# -----------------------------------------------------------------------------
def test_1_unknown_published_uses_first_seen_at(db_session):
    make_companies(db_session)
    service = SupplyChainService()

    rel = Relationship(id=1, source_id=10, target_id=20, type="SUPPLIES_TO", status="verified")
    # published_at is None, first_seen_at = 2026-10-30 08:00:00 UTC
    t_seen = datetime(2026, 10, 30, 8, 0, 0, tzinfo=UTC)
    ev = RelationshipEvidence(
        id=1,
        relationship_id=1,
        excerpt="Official supplier recognition",
        source_document="Supplier Day",
        published_at=None,
        first_seen_at=t_seen,
        verification_status="verified",
        evidence_hash="ev-hash-1",
    )
    rel.evidences = [ev]
    db_session.add_all([rel, ev])
    db_session.commit()

    # Available at should be first_seen_at
    assert get_evidence_available_at(ev) == t_seen

    # A) Before first_seen_at (e.g., 2025-06-12 12:00:00 UTC) -> Excluded
    as_of_before = datetime(2025, 6, 12, 12, 0, 0, tzinfo=UTC)
    edge_before = service.normalize_relationship(rel, as_of=as_of_before)
    assert edge_before is None, "Evidence must NOT be backdated before first_seen_at"

    # B) Exactly before first_seen_at on same day (07:59:59 UTC) -> Excluded
    as_of_just_before = datetime(2026, 10, 30, 7, 59, 59, tzinfo=UTC)
    edge_just_before = service.normalize_relationship(rel, as_of=as_of_just_before)
    assert edge_just_before is None

    # C) On or after first_seen_at (e.g., 2026-10-30 09:00:00 UTC) -> Included
    as_of_after = datetime(2026, 10, 30, 9, 0, 0, tzinfo=UTC)
    edge_after = service.normalize_relationship(rel, as_of=as_of_after)
    assert edge_after is not None
    assert edge_after.supplier_id == 10
    assert edge_after.customer_id == 20
    assert edge_after.latest_evidence_published_at == t_seen


# -----------------------------------------------------------------------------
# Test 2: 공개시각과 최초 확인시각 모두 존재 -> max(published_at, first_seen_at) 적용
# -----------------------------------------------------------------------------
def test_2_both_timestamps_uses_max(db_session):
    make_companies(db_session)
    service = SupplyChainService()

    t_pub = datetime(2026, 5, 1, 10, 0, 0, tzinfo=UTC)
    t_seen = datetime(2026, 6, 1, 15, 0, 0, tzinfo=UTC)

    rel = Relationship(id=2, source_id=10, target_id=20, type="SUPPLIES_TO", status="verified")
    ev = RelationshipEvidence(
        id=2,
        relationship_id=2,
        excerpt="Dual timestamp evidence",
        source_document="Announcement",
        published_at=t_pub,
        first_seen_at=t_seen,
        verification_status="verified",
        evidence_hash="ev-hash-2",
    )
    rel.evidences = [ev]
    db_session.add_all([rel, ev])
    db_session.commit()

    # Availability must be max(t_pub, t_seen) = t_seen
    expected_avail = max(t_pub, t_seen)
    assert get_evidence_available_at(ev) == expected_avail

    # Between published_at and first_seen_at -> Excluded because observation was later
    as_of_mid = datetime(2026, 5, 15, 12, 0, 0, tzinfo=UTC)
    assert service.normalize_relationship(rel, as_of=as_of_mid) is None

    # After both timestamps -> Included
    as_of_after = datetime(2026, 6, 2, 12, 0, 0, tzinfo=UTC)
    edge = service.normalize_relationship(rel, as_of=as_of_after)
    assert edge is not None
    assert edge.latest_evidence_published_at == t_seen


# -----------------------------------------------------------------------------
# Test 3: 두 시각 모두 미상 -> 관계 제외
# -----------------------------------------------------------------------------
def test_3_both_timestamps_none_excludes_relationship(db_session):
    make_companies(db_session)
    service = SupplyChainService()

    rel = Relationship(id=3, source_id=10, target_id=20, type="SUPPLIES_TO", status="verified")
    ev = RelationshipEvidence(
        id=3,
        relationship_id=3,
        excerpt="No timestamps evidence",
        source_document="Undated note",
        published_at=None,
        first_seen_at=None,
        verification_status="verified",
        evidence_hash="ev-hash-3",
    )
    rel.evidences = [ev]
    db_session.add_all([rel, ev])
    db_session.commit()

    assert get_evidence_available_at(ev) is None

    as_of = datetime(2026, 11, 1, 12, 0, 0, tzinfo=UTC)
    assert service.normalize_relationship(rel, as_of=as_of) is None


# -----------------------------------------------------------------------------
# Test 4: 미승인 근거는 시각이 유효해도 제외
# -----------------------------------------------------------------------------
def test_4_unverified_evidence_excluded_even_with_valid_timestamps(db_session):
    make_companies(db_session)
    service = SupplyChainService()

    t_seen = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    rel = Relationship(id=4, source_id=10, target_id=20, type="SUPPLIES_TO", status="verified")
    ev_proposed = RelationshipEvidence(
        id=4,
        relationship_id=4,
        excerpt="Proposed evidence",
        source_document="Unverified source",
        published_at=None,
        first_seen_at=t_seen,
        verification_status="proposed",  # Not verified!
        evidence_hash="ev-hash-4",
    )
    rel.evidences = [ev_proposed]
    db_session.add_all([rel, ev_proposed])
    db_session.commit()

    as_of_after = datetime(2026, 2, 1, 12, 0, 0, tzinfo=UTC)
    assert service.normalize_relationship(rel, as_of=as_of_after) is None, "Proposed evidence must be excluded"


# -----------------------------------------------------------------------------
# Test 5: 기존 공개시각 보유 자료의 의도된 호환성 유지 여부 확인
# -----------------------------------------------------------------------------
def test_5_existing_records_with_only_published_at_preserve_backward_compatibility(db_session):
    make_companies(db_session)
    service = SupplyChainService()

    t_pub = datetime(2024, 8, 15, 9, 30, 0, tzinfo=UTC)
    rel = Relationship(id=5, source_id=10, target_id=20, type="SUPPLIES_TO", status="verified")
    # Existing record: published_at is present, first_seen_at is None
    ev_existing = RelationshipEvidence(
        id=5,
        relationship_id=5,
        excerpt="DART semi-annual report",
        source_document="DART",
        published_at=t_pub,
        first_seen_at=None,
        verification_status="verified",
        evidence_hash="ev-hash-5",
    )
    rel.evidences = [ev_existing]
    db_session.add_all([rel, ev_existing])
    db_session.commit()

    # Must resolve cleanly to published_at
    assert get_evidence_available_at(ev_existing) == t_pub

    # Before publication -> Excluded
    as_of_before = datetime(2024, 8, 14, 12, 0, 0, tzinfo=UTC)
    assert service.normalize_relationship(rel, as_of=as_of_before) is None

    # After publication -> Included
    as_of_after = datetime(2024, 8, 16, 12, 0, 0, tzinfo=UTC)
    edge = service.normalize_relationship(rel, as_of=as_of_after)
    assert edge is not None
    assert edge.latest_evidence_published_at == t_pub

