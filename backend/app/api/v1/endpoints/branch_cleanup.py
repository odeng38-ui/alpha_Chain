"""Admin API endpoints for Neon Branch Cleanup Advisor.

Protected by `require_admin`.
Provides read-only branch analysis reports to prevent accumulation of Neon Preview branches.
Strictly read-only: NO branch delete actions are implemented.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.adapters.neon_branch_reader import (
    NeonAuthError,
    NeonConfigError,
    NeonNotFoundError,
    NeonRateLimitError,
    NeonReaderError,
)
from app.security import require_admin
from app.services.neon_branch_cleanup_advisor import (
    BranchCleanupReport,
    NeonBranchCleanupAdvisor,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin/branch-cleanup",
    tags=["Admin Branch Cleanup Advisor"],
    dependencies=[Depends(require_admin)],
)

_advisor_instance: Optional[NeonBranchCleanupAdvisor] = None


def get_cleanup_advisor() -> NeonBranchCleanupAdvisor:
    global _advisor_instance
    if _advisor_instance is None:
        _advisor_instance = NeonBranchCleanupAdvisor()
    return _advisor_instance


class ManualBranchAnalysisRequest(BaseModel):
    """Payload for analyzing manually provided Neon branches (offline/mock mode)."""

    branches: list[dict[str, Any]] = Field(
        ...,
        description="List of raw Neon branch objects or dictionary with 'branches' key",
    )
    verified_branches: list[str] = Field(
        default_factory=list,
        description="List of branch names or IDs explicitly verified for data preservation",
    )


def _map_reader_exception_to_http(exc: Exception) -> HTTPException:
    if isinstance(exc, NeonConfigError):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        )
    if isinstance(exc, NeonAuthError):
        return HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Neon API authentication failed. Verify credentials in server environment.",
        )
    if isinstance(exc, NeonNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )
    if isinstance(exc, NeonRateLimitError):
        return HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Neon API rate limit exceeded. Please retry later.",
        )
    if isinstance(exc, NeonReaderError):
        return HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Neon provider error: {exc}",
        )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Internal error processing branch cleanup report.",
    )


@router.get(
    "/report",
    response_model=BranchCleanupReport,
    summary="Generate read-only cleanup analysis for Neon preview branches",
)
def get_branch_cleanup_report(
    advisor: NeonBranchCleanupAdvisor = Depends(get_cleanup_advisor),
) -> BranchCleanupReport:
    """Analyze all Neon branches against GitHub PR and Vercel deployment statuses."""
    try:
        return advisor.generate_report()
    except Exception as exc:
        raise _map_reader_exception_to_http(exc) from exc


@router.post(
    "/analyze",
    response_model=BranchCleanupReport,
    summary="Analyze manually supplied Neon branch list (supports offline & testing mode)",
)
def analyze_manual_branches(
    request: ManualBranchAnalysisRequest,
    advisor: NeonBranchCleanupAdvisor = Depends(get_cleanup_advisor),
) -> BranchCleanupReport:
    """Analyze a manually provided list of Neon branches."""
    try:
        verified_set = set(request.verified_branches)
        return advisor.generate_report(
            manual_branches=request.branches,
            verified_branches=verified_set,
        )
    except Exception as exc:
        raise _map_reader_exception_to_http(exc) from exc
