"""S2-B2: 이벤트 증거 등록·검증 및 앵커기업 연결 서비스.

설계 원칙:
- DB 세션은 외부에서 주입받는다. 모듈 import 시 외부 API를 호출하지 않는다.
- 사실(extracted_facts)과 해석(interpretations)을 분리한다.
- published_at이 불명확하면 None으로 유지한다. 임의의 시각을 생성하지 않는다.
- as_of 기준 미래 공개 증거는 조회 결과에서 제외한다.
- CIK 충돌(동일 식별자가 여러 company_id와 매핑)이면 연결을 중단한다.
- 증거 없이 앵커기업을 verified로 처리하지 않는다.
- 여러 앵커기업이 존재하면 단일 앵커 분석에서 모호성 결과를 반환한다.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.schema import (
    Company,
    EventCompanyLink,
    GlobalEvent,
    GlobalEventEvidence,
    IdentifierMap,
)
from app.services.identifier_service import (
    EXCLUSIVE_IDENTIFIER_SOURCES,
    IdentifierConflictError,
)

# ---------------------------------------------------------------------------
# 허용 증거 유형 및 역할
# ---------------------------------------------------------------------------
EVIDENCE_TYPES = {"OFFICIAL", "REGULATORY", "NEWS", "ANALYSIS"}
COMPANY_LINK_ROLES = {"ANCHOR", "INVESTOR", "FACILITY_OWNER", "CUSTOMER"}
VERIFICATION_STATUSES = {"proposed", "verified", "rejected"}


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    """timezone-aware UTC 현재 시각."""
    return datetime.now(tz=timezone.utc)


def _compute_evidence_hash(source_url: Optional[str], excerpt: Optional[str]) -> str:
    """SHA-256(source_url + '|' + excerpt) 중복 방지 키."""
    raw = f"{source_url or ''}|{excerpt or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _assert_event_exists(db: Session, event_id: int) -> GlobalEvent:
    event = db.get(GlobalEvent, event_id)
    if event is None:
        raise LookupError(f"GlobalEvent(id={event_id}) not found")
    return event


# ---------------------------------------------------------------------------
# 예외
# ---------------------------------------------------------------------------

class EvidenceDuplicateError(ValueError):
    """동일 증거가 이미 등록된 경우."""


class AnchorIdentifierConflictError(ValueError):
    """CIK 등 배타적 식별자가 여러 기업과 충돌하여 연결 중단."""


class EvidenceRequiredError(ValueError):
    """verified 앵커 연결에 verified 증거가 필요한데 없는 경우."""


class AmbiguousAnchorError(ValueError):
    """단일 앵커 분석인데 여러 앵커가 존재하는 경우."""


# ---------------------------------------------------------------------------
# GlobalEventEvidenceService
# ---------------------------------------------------------------------------

class GlobalEventEvidenceService:
    """이벤트 증거 등록·조회·검증 서비스."""

    @staticmethod
    def register(
        db: Session,
        *,
        event_id: int,
        evidence_type: str,
        source_url: Optional[str] = None,
        excerpt: Optional[str] = None,
        source_document: Optional[str] = None,
        location: Optional[str] = None,
        published_at: Optional[datetime] = None,
        extracted_facts: Optional[dict[str, Any]] = None,
        interpretations: Optional[dict[str, Any]] = None,
        collected_at: Optional[datetime] = None,
    ) -> GlobalEventEvidence:
        """증거를 등록한다. 중복이면 EvidenceDuplicateError를 일으킨다.

        규칙:
        - published_at은 timezone-aware이어야 한다. None이어도 된다 (불명확한 경우).
        - extracted_facts와 interpretations는 별도 필드에 저장한다 (혼입 금지).
        - collected_at이 None이면 _utcnow()를 사용한다.
        """
        if evidence_type not in EVIDENCE_TYPES:
            raise ValueError(f"evidence_type must be one of {EVIDENCE_TYPES}")
        _assert_event_exists(db, event_id)

        # published_at timezone 검증
        if published_at is not None and published_at.tzinfo is None:
            raise ValueError("published_at must be timezone-aware (UTC). Use datetime.now(tz=timezone.utc) or similar.")

        effective_collected_at = collected_at or _utcnow()

        evidence_hash = _compute_evidence_hash(source_url, excerpt)
        existing = (
            db.query(GlobalEventEvidence)
            .filter(GlobalEventEvidence.evidence_hash == evidence_hash)
            .first()
        )
        if existing is not None:
            raise EvidenceDuplicateError(
                f"Evidence with hash {evidence_hash[:12]}… already registered (id={existing.id})"
            )

        evidence = GlobalEventEvidence(
            event_id=event_id,
            evidence_type=evidence_type,
            source_document=source_document,
            source_url=source_url,
            location=location,
            excerpt=excerpt,
            published_at=published_at,
            collected_at=effective_collected_at,
            extracted_facts=extracted_facts or {},
            interpretations=interpretations or {},
            verification_status="proposed",
            evidence_hash=evidence_hash,
        )
        db.add(evidence)
        db.flush()
        return evidence

    @staticmethod
    def get_evidence_before(
        db: Session,
        event_id: int,
        as_of: datetime,
    ) -> list[GlobalEventEvidence]:
        """as_of 기준 이전에 공개된 증거만 반환한다.

        - published_at이 as_of 이후이면 제외한다.
        - published_at이 NULL이면 '공개 시각 불명확'으로 간주하고 제외한다
          (보수적 처리: 미래정보 차단 원칙 준수).
        - as_of는 timezone-aware이어야 한다.
        """
        if as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")
        rows = (
            db.query(GlobalEventEvidence)
            .filter(
                GlobalEventEvidence.event_id == event_id,
                GlobalEventEvidence.published_at.isnot(None),
                GlobalEventEvidence.published_at <= as_of,
            )
            .all()
        )
        return rows

    @staticmethod
    def detect_duplicate(
        db: Session,
        source_url: Optional[str],
        excerpt: Optional[str],
    ) -> Optional[GlobalEventEvidence]:
        """동일 증거가 이미 존재하면 반환, 없으면 None."""
        evidence_hash = _compute_evidence_hash(source_url, excerpt)
        return (
            db.query(GlobalEventEvidence)
            .filter(GlobalEventEvidence.evidence_hash == evidence_hash)
            .first()
        )


# ---------------------------------------------------------------------------
# EventCompanyLinkService
# ---------------------------------------------------------------------------

class EventCompanyLinkService:
    """이벤트 ↔ 앵커기업 연결 서비스.

    연결 규칙:
    1. evidence_id는 필수 — 근거 없는 연결 방지.
    2. 앵커기업은 IdentifierMap으로 확인한다. 이름만 일치한다고 자동 연결하지 않는다.
    3. CIK 등 배타적 식별자 충돌 시 AnchorIdentifierConflictError.
    4. verified 연결은 evidence.verification_status == 'verified'가 필요하다.
    """

    @staticmethod
    def link(
        db: Session,
        *,
        event_id: int,
        company_id: int,
        role: str,
        evidence_id: int,
        verification_status: str = "proposed",
        confidence: Optional[float] = None,
    ) -> EventCompanyLink:
        """이벤트와 기업을 연결한다.

        - verified 연결 요청 시 evidence가 verified이어야 한다.
        - role이 허용 목록에 없으면 ValueError.
        - (event_id, company_id, role) 조합이 이미 존재하면 기존 행을 반환한다 (멱등).
        """
        if role not in COMPANY_LINK_ROLES:
            raise ValueError(f"role must be one of {COMPANY_LINK_ROLES}")
        if verification_status not in VERIFICATION_STATUSES:
            raise ValueError(f"verification_status must be one of {VERIFICATION_STATUSES}")

        _assert_event_exists(db, event_id)

        company = db.get(Company, company_id)
        if company is None:
            raise LookupError(f"Company(id={company_id}) not found")

        evidence = db.get(GlobalEventEvidence, evidence_id)
        if evidence is None:
            raise LookupError(f"GlobalEventEvidence(id={evidence_id}) not found")
        if evidence.event_id != event_id:
            raise ValueError("evidence.event_id does not match event_id")

        # verified 연결에는 verified 증거가 필요하다
        if verification_status == "verified" and evidence.verification_status != "verified":
            raise EvidenceRequiredError(
                f"Cannot set link to verified: evidence(id={evidence_id}) is {evidence.verification_status!r}"
            )

        # 멱등 처리: 동일 (event_id, company_id, role)이 이미 존재하면 반환
        existing = (
            db.query(EventCompanyLink)
            .filter(
                EventCompanyLink.event_id == event_id,
                EventCompanyLink.company_id == company_id,
                EventCompanyLink.role == role,
            )
            .first()
        )
        if existing is not None:
            return existing

        link = EventCompanyLink(
            event_id=event_id,
            company_id=company_id,
            role=role,
            evidence_id=evidence_id,
            verification_status=verification_status,
            confidence=confidence,
        )
        db.add(link)
        db.flush()
        return link

    @staticmethod
    def link_by_identifier(
        db: Session,
        *,
        event_id: int,
        source: str,
        source_id_value: str,
        role: str,
        evidence_id: int,
        verification_status: str = "proposed",
        confidence: Optional[float] = None,
    ) -> EventCompanyLink:
        """식별자(CIK 등)로 기업을 조회한 후 연결한다.

        규칙:
        - 동일 source/value에 여러 company_id가 활성 상태면 CIK 충돌 → AnchorIdentifierConflictError.
        - 식별자가 없으면 LookupError.
        - 이름만 일치하는 연결은 이 메서드에서 제공하지 않는다.
        """
        from app.services.identifier_service import normalize_identifier

        normalized_source = source.strip().upper()
        normalized_value = normalize_identifier(normalized_source, source_id_value)

        active_maps = (
            db.query(IdentifierMap)
            .filter(
                IdentifierMap.source == normalized_source,
                IdentifierMap.source_id_value == normalized_value,
                IdentifierMap.effective_to.is_(None),
            )
            .all()
        )

        if not active_maps:
            raise LookupError(
                f"No active IdentifierMap found for {source}={source_id_value}"
            )

        # CIK 충돌: 동일 배타 식별자에 여러 company_id가 매핑된 경우
        if normalized_source in EXCLUSIVE_IDENTIFIER_SOURCES:
            company_ids = {m.company_id for m in active_maps}
            if len(company_ids) > 1:
                raise AnchorIdentifierConflictError(
                    f"Identifier {source}={source_id_value} maps to multiple companies: {company_ids}. "
                    "Aborting link to prevent incorrect anchor assignment."
                )

        company_id = active_maps[0].company_id
        return EventCompanyLinkService.link(
            db,
            event_id=event_id,
            company_id=company_id,
            role=role,
            evidence_id=evidence_id,
            verification_status=verification_status,
            confidence=confidence,
        )

    @staticmethod
    def get_verified_anchors(
        db: Session,
        event_id: int,
        as_of: Optional[datetime] = None,
    ) -> list[EventCompanyLink]:
        """검증된 앵커기업 연결 목록을 반환한다.

        - as_of가 지정되면 evidence.published_at <= as_of인 연결만 반환한다 (미래정보 차단).
        - as_of는 timezone-aware이어야 한다.
        """
        if as_of is not None and as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")

        q = (
            db.query(EventCompanyLink)
            .join(GlobalEventEvidence, EventCompanyLink.evidence_id == GlobalEventEvidence.id)
            .filter(
                EventCompanyLink.event_id == event_id,
                EventCompanyLink.role == "ANCHOR",
                EventCompanyLink.verification_status == "verified",
            )
        )

        if as_of is not None:
            q = q.filter(
                GlobalEventEvidence.published_at.isnot(None),
                GlobalEventEvidence.published_at <= as_of,
            )

        return q.all()

    @staticmethod
    def get_single_anchor(
        db: Session,
        event_id: int,
        as_of: Optional[datetime] = None,
    ) -> EventCompanyLink:
        """단일 앵커 분석용: 정확히 1개의 verified ANCHOR가 있어야 한다.

        - 앵커가 없으면 LookupError.
        - 앵커가 2개 이상이면 AmbiguousAnchorError.
        """
        anchors = EventCompanyLinkService.get_verified_anchors(db, event_id, as_of=as_of)
        if len(anchors) == 0:
            raise LookupError(f"No verified ANCHOR found for event_id={event_id}")
        if len(anchors) > 1:
            ids = [a.company_id for a in anchors]
            raise AmbiguousAnchorError(
                f"Multiple verified anchors for event_id={event_id}: company_ids={ids}. "
                "Single-anchor analysis is ambiguous."
            )
        return anchors[0]
