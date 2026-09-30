from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.schema import Relationship
from app.security import require_admin
from app.services.audit_service import record_audit
from app.services.relationship_service import (
    RELATIONSHIP_TYPES,
    RelationshipCandidate,
    RelationshipExtractionService,
)

router = APIRouter(prefix="/relationships", tags=["Relationships"])
service = RelationshipExtractionService()


class ExtractRequest(BaseModel):
    source_company_id: int
    text: str = Field(min_length=1)
    source_document: str = Field(min_length=1)
    source_url: Optional[str] = None
    filing_id: Optional[str] = None
    published_at: Optional[datetime] = None


class CandidateRequest(BaseModel):
    source_company_id: int
    target_name: str
    relationship_type: str
    evidence_text: str = Field(min_length=1)
    evidence_location: Optional[str] = None
    confidence: float = Field(ge=0, le=1)
    source_document: str = Field(min_length=1)
    source_url: Optional[str] = None
    filing_id: Optional[str] = None
    published_at: Optional[datetime] = None
    extractor_version: str = "llm_v1"


class ReviewRequest(BaseModel):
    status: str
    reviewer: str = Field(min_length=1)


@router.post("/extract", dependencies=[Depends(require_admin)])
def extract_relationships(req: ExtractRequest, db: Session = Depends(get_db)):
    return service.extract_and_persist(
        db, req.source_company_id, req.text, source_document=req.source_document,
        source_url=req.source_url, filing_id=req.filing_id, published_at=req.published_at,
    )


@router.post("/candidates", dependencies=[Depends(require_admin)])
def submit_llm_candidate(req: CandidateRequest, db: Session = Depends(get_db)):
    if req.relationship_type not in RELATIONSHIP_TYPES:
        raise HTTPException(status_code=422, detail="unsupported relationship type")
    candidate = RelationshipCandidate(
        req.target_name, req.relationship_type, req.evidence_text,
        req.evidence_location, req.confidence,
    )
    return service.persist_candidates(
        db, req.source_company_id, [candidate], req.source_document,
        req.source_url, req.filing_id, req.published_at, req.extractor_version,
    )


@router.get("/review-queue")
def review_queue(
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    rows = db.query(Relationship).filter(Relationship.status == "proposed").order_by(
        Relationship.confidence.desc(), Relationship.id,
    ).offset(offset).limit(limit).all()
    return [{
        "id": row.id, "source_id": row.source_id, "target_id": row.target_id,
        "type": row.type, "confidence": row.confidence,
        "evidence_count": len(row.evidences),
        "evidences": [{"text": ev.excerpt, "source_document": ev.source_document,
                       "source_url": ev.source_url, "published_at": ev.published_at}
                      for ev in row.evidences],
    } for row in rows]


@router.patch("/{relationship_id}/review")
def review_relationship(relationship_id: int, req: ReviewRequest, db: Session = Depends(get_db),
                        admin_actor: str = Depends(require_admin)):
    if req.status not in {"verified", "rejected", "expired"}:
        raise HTTPException(status_code=422, detail="status must be verified, rejected, or expired")
    row = db.get(Relationship, relationship_id)
    if row is None:
        raise HTTPException(status_code=404, detail="relationship not found")
    if not row.evidences:
        raise HTTPException(status_code=409, detail="relationship has no evidence")
    before_state = {
        "status": row.status,
        "reviewed_by": row.reviewed_by,
        "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
    }
    row.status = req.status
    row.reviewed_by = f"{req.reviewer}:{admin_actor}"
    row.reviewed_at = datetime.utcnow()
    record_audit(
        db,
        actor=req.reviewer,
        action=f"relationship.{req.status}",
        resource_type="relationship",
        resource_id=row.id,
        before_state=before_state,
        after_state={"status": row.status, "reviewed_by": row.reviewed_by,
                     "reviewed_at": row.reviewed_at.isoformat()},
    )
    db.commit()
    return {"id": row.id, "status": row.status, "reviewed_by": row.reviewed_by}
