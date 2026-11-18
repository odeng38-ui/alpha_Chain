"""Neon Branch Cleanup Advisor Service.

Read-only analytical engine that evaluates Neon Preview database branches against:
1. Neon branch metadata (default, backup, active compute endpoints)
2. GitHub Pull Request status (OPEN, MERGED, CLOSED, NOT_FOUND, UNKNOWN)
3. Vercel Preview deployment status (ACTIVE_PREVIEW, PAST_PREVIEW, FAILED_PREVIEW, UNCONFIRMED_OR_DELETED, NOT_FOUND)
4. Data preservation verification status

Strict safety constraints:
- Classifies branches into PROTECTED, REVIEW_REQUIRED, DELETE_CANDIDATE
- Defaults to REVIEW_REQUIRED whenever information or data preservation is unconfirmed
- NO delete execution, NO write mutations, NO automated schedules
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional, Set

from pydantic import BaseModel, Field

from app.adapters.github_pr_reader import GitHubPrInfo, GitHubPrReader
from app.adapters.neon_branch_reader import NeonBranchInfo, NeonBranchReader
from app.adapters.vercel_deployment_reader import (
    VercelDeploymentInfo,
    VercelDeploymentReader,
)
from app.config import settings

logger = logging.getLogger(__name__)


class BranchClassification(str, Enum):
    PROTECTED = "PROTECTED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    DELETE_CANDIDATE = "DELETE_CANDIDATE"


class BranchAnalysisItem(BaseModel):
    """Detailed audit item for a single database branch."""

    branch_id: str
    name: str
    classification: BranchClassification
    reasons: list[str] = Field(default_factory=list)
    confidence: str  # "verified", "incomplete", "provisional"
    pr_number: Optional[int] = None
    pr_state: str  # "OPEN", "MERGED", "CLOSED", "NOT_FOUND", "UNKNOWN"
    vercel_state: str  # "ACTIVE_PREVIEW", "PAST_PREVIEW", "FAILED_PREVIEW", "UNCONFIRMED_OR_DELETED", "NOT_FOUND"
    data_preservation_verified: bool = False
    has_active_endpoint: bool = False
    created_at: Optional[str] = None
    last_activity_at: Optional[str] = None


class BranchCleanupReport(BaseModel):
    """Overall summary report for Neon branches."""

    project: str = "alpha_chain"
    generated_at: str
    total_branches: int
    protected: int
    review_required: int
    delete_candidates: int
    branches: list[BranchAnalysisItem] = Field(default_factory=list)


class NeonBranchCleanupAdvisor:
    """Service that cross-references Neon, GitHub, and Vercel to advise on branch cleanup."""

    def __init__(
        self,
        neon_reader: Optional[NeonBranchReader] = None,
        github_reader: Optional[GitHubPrReader] = None,
        vercel_reader: Optional[VercelDeploymentReader] = None,
        protected_branches: Optional[list[str]] = None,
    ) -> None:
        self.neon_reader = neon_reader or NeonBranchReader()
        self.github_reader = github_reader or GitHubPrReader()
        self.vercel_reader = vercel_reader or VercelDeploymentReader()
        self.protected_branches = set(
            b.lower()
            for b in (
                protected_branches
                if protected_branches is not None
                else settings.neon_protected_branch_list
            )
        )
        # Always guarantee core branch protections
        self.protected_branches.update({"main", "master", "production"})

    def classify_branch(
        self,
        branch: NeonBranchInfo,
        pr_info: GitHubPrInfo,
        vercel_info: VercelDeploymentInfo,
        data_preservation_verified: bool = False,
    ) -> BranchAnalysisItem:
        """Classify a single branch into PROTECTED, REVIEW_REQUIRED, or DELETE_CANDIDATE."""
        name_lower = branch.name.lower().strip()
        reasons: list[str] = []

        # ----------------------------------------------------------------------
        # Rule F04: Check for automatic protection
        # ----------------------------------------------------------------------
        is_protected = False

        if branch.is_default:
            is_protected = True
            reasons.append("default_branch")

        if name_lower in self.protected_branches:
            is_protected = True
            reasons.append("protected_name")

        if name_lower.startswith(("backup-", "backup_", "bak-")):
            is_protected = True
            reasons.append("backup_branch")

        if pr_info.state == "OPEN":
            is_protected = True
            reasons.append("open_pull_request")

        if vercel_info.state == "ACTIVE_PREVIEW":
            is_protected = True
            reasons.append("active_preview_deployment")

        if is_protected:
            return BranchAnalysisItem(
                branch_id=branch.branch_id,
                name=branch.name,
                classification=BranchClassification.PROTECTED,
                reasons=reasons,
                confidence="verified",
                pr_number=pr_info.pr_number,
                pr_state=pr_info.state,
                vercel_state=vercel_info.state,
                data_preservation_verified=data_preservation_verified,
                has_active_endpoint=branch.has_active_endpoint,
                created_at=branch.created_at,
                last_activity_at=branch.updated_at,
            )

        # ----------------------------------------------------------------------
        # Rule F05: Check for DELETE_CANDIDATE
        # All conditions MUST hold:
        # 1. Not protected (checked above)
        # 2. PR closed or merged
        # 3. No active preview deployment
        # 4. No active compute endpoint
        # 5. Data preservation explicitly verified
        # ----------------------------------------------------------------------
        pr_closed_or_merged = pr_info.state in ("MERGED", "CLOSED")
        no_active_dep = vercel_info.state in (
            "PAST_PREVIEW",
            "FAILED_PREVIEW",
            "UNCONFIRMED_OR_DELETED",
            "NOT_FOUND",
        )
        no_active_compute = not branch.has_active_endpoint

        if (
            pr_closed_or_merged
            and no_active_dep
            and no_active_compute
            and data_preservation_verified
        ):
            return BranchAnalysisItem(
                branch_id=branch.branch_id,
                name=branch.name,
                classification=BranchClassification.DELETE_CANDIDATE,
                reasons=[
                    f"pr_{pr_info.state.lower()}",
                    "no_active_deployment",
                    "no_active_compute",
                    "data_preservation_verified",
                ],
                confidence="verified",
                pr_number=pr_info.pr_number,
                pr_state=pr_info.state,
                vercel_state=vercel_info.state,
                data_preservation_verified=True,
                has_active_endpoint=branch.has_active_endpoint,
                created_at=branch.created_at,
                last_activity_at=branch.updated_at,
            )

        # ----------------------------------------------------------------------
        # Rule F05 Fallback: REVIEW_REQUIRED
        # Explain all missing or uncertain criteria
        # ----------------------------------------------------------------------
        review_reasons: list[str] = []

        if not data_preservation_verified:
            review_reasons.append("data_preservation_not_verified")

        if pr_info.state in ("NOT_FOUND", "UNKNOWN"):
            review_reasons.append("unresolved_pr_relationship")

        if not no_active_compute:
            review_reasons.append("active_compute_endpoint_present")

        if vercel_info.state == "UNCONFIRMED_OR_DELETED":
            review_reasons.append("deployment_status_unconfirmed")

        if not review_reasons:
            review_reasons.append("incomplete_criteria_verification")

        return BranchAnalysisItem(
            branch_id=branch.branch_id,
            name=branch.name,
            classification=BranchClassification.REVIEW_REQUIRED,
            reasons=review_reasons,
            confidence="incomplete",
            pr_number=pr_info.pr_number,
            pr_state=pr_info.state,
            vercel_state=vercel_info.state,
            data_preservation_verified=data_preservation_verified,
            has_active_endpoint=branch.has_active_endpoint,
            created_at=branch.created_at,
            last_activity_at=branch.updated_at,
        )

    def generate_report(
        self,
        manual_branches: Optional[list[dict[str, Any]] | dict[str, Any]] = None,
        verified_branches: Optional[Set[str]] = None,
    ) -> BranchCleanupReport:
        """Fetch/parse branches, cross-reference with GitHub and Vercel, and build classification report."""
        if manual_branches is not None:
            branches = self.neon_reader.parse_manual_branches(manual_branches)
        else:
            branches = self.neon_reader.fetch_branches()

        verified_set = verified_branches or set()
        items: list[BranchAnalysisItem] = []

        count_protected = 0
        count_review = 0
        count_delete = 0

        for b in branches:
            pr_info = self.github_reader.get_pr_status_for_branch(b.name)
            vercel_info = self.vercel_reader.get_deployment_status_for_branch(
                b.name, pr_info.pr_number
            )

            is_data_verified = (
                b.name in verified_set
                or b.branch_id in verified_set
                or b.name.lower() in verified_set
            )

            item = self.classify_branch(
                branch=b,
                pr_info=pr_info,
                vercel_info=vercel_info,
                data_preservation_verified=is_data_verified,
            )

            if item.classification == BranchClassification.PROTECTED:
                count_protected += 1
            elif item.classification == BranchClassification.REVIEW_REQUIRED:
                count_review += 1
            elif item.classification == BranchClassification.DELETE_CANDIDATE:
                count_delete += 1

            items.append(item)

        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        return BranchCleanupReport(
            project="alpha_chain",
            generated_at=now_iso,
            total_branches=len(items),
            protected=count_protected,
            review_required=count_review,
            delete_candidates=count_delete,
            branches=items,
        )
