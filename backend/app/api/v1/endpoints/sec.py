"""Admin endpoint for bounded single-filing official SEC EDGAR ingestion."""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.adapters.sec_adapter import (
    SecAdapterError,
    SecAuthError,
    SecEdgarAdapter,
    SecNotFoundError,
    SecRateLimitError,
    validate_sec_user_agent,
)
from app.config import settings
from app.db.session import get_db
from app.security import require_admin
from app.services.sec_filing_service import SecFilingService, SecFilingValidationError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sec", tags=["SEC Official Filing Ingestion"], dependencies=[Depends(require_admin)])


class SecIngestOneRequest(BaseModel):
    """Request schema for ingesting a single SEC filing."""

    cik: str = Field(..., description="SEC Central Index Key (CIK) of the US issuing company", example="0000723125")
    form_type: str = Field("8-K", description="Filing form type to retrieve (e.g. 8-K, 10-Q, 10-K)", example="8-K")
    dry_run: bool = Field(True, description="When true (default), previews binding without persisting any DB rows")
    user_agent: Optional[str] = Field(
        None,
        description="Explicit contactable User-Agent string adhering to SEC fair access policy",
        example="AlphaChain/1.0 (contact@alphachain.org)",
    )
    event_kind: Optional[str] = Field(
        None,
        description="Explicit event kind if verified; defaults to unclassified if omitted",
        example="UNCLASSIFIED_FILING",
    )


class SecIngestOneResponse(BaseModel):
    """Response schema for single SEC filing ingestion."""

    status: str
    cik: str
    form_type: str
    dry_run: bool
    persisted: bool
    company_id: Optional[int] = None
    company_name: Optional[str] = None
    event_id: Optional[int] = None
    evidence_id: Optional[int] = None
    anchor_link_id: Optional[int] = None
    verification_status: Optional[str] = None
    reason: Optional[str] = None
    filing_details: Optional[dict[str, Any]] = None


@router.post(
    "/ingest-one",
    response_model=SecIngestOneResponse,
    summary="Ingest or preview single official SEC filing for an issuing US company",
)
def ingest_one_sec_filing(
    req: SecIngestOneRequest,
    db: Session = Depends(get_db),
) -> SecIngestOneResponse:
    """Retrieve and ingest the latest official SEC filing for a US company.

    - By default, `dry_run=True` returns preview binding and hold reasons with zero DB writes or flushes.
    - Explicit `dry_run=False` persists GlobalEvent, Evidence, and Anchor candidate in 'proposed' status.
    - Requires privileged administrator credentials (`X-Admin-Key` header).
    """
    raw_ua = req.user_agent or getattr(settings, "SEC_USER_AGENT", "")
    try:
        validated_ua = validate_sec_user_agent(raw_ua)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid SEC User-Agent: {exc}",
        )

    try:
        adapter = SecEdgarAdapter(user_agent=validated_ua)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to initialize SEC adapter: {exc}",
        )

    try:
        result = SecFilingService.ingest_latest_filing_by_cik(
            db,
            cik=req.cik,
            form_type=req.form_type,
            dry_run=req.dry_run,
            adapter=adapter,
            event_kind=req.event_kind,
        )
    except SecNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except SecAuthError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except SecRateLimitError as exc:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc))
    except (SecFilingValidationError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except SecAdapterError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    except Exception as exc:
        logger.exception("Unexpected error during SEC ingestion: %s", exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal ingestion failure")

    return SecIngestOneResponse(
        status=result.get("status", "unknown"),
        cik=req.cik,
        form_type=req.form_type,
        dry_run=result.get("dry_run", req.dry_run),
        persisted=result.get("persisted", False),
        company_id=result.get("company_id"),
        company_name=result.get("company_name"),
        event_id=result.get("event_id"),
        evidence_id=result.get("evidence_id"),
        anchor_link_id=result.get("anchor_link_id"),
        verification_status=result.get("verification_status"),
        reason=result.get("reason"),
        filing_details=result.get("filing") or result,
    )
