"""Unit tests for Neon Branch Cleanup Advisor (AC-ADMIN-NBCA-01).

Covers all 9 mandatory test requirements:
1. 'main' branch protected
2. Backup branch ('backup-*') protected
3. OPEN PR linked branch protected
4. Closed/Merged PR with unverified DB data -> REVIEW_REQUIRED
5. Insufficient linking info (UNKNOWN/NOT_FOUND PR) -> REVIEW_REQUIRED
6. All conditions verified (closed/merged + no deployment + no compute + verified data) -> DELETE_CANDIDATE
7. Neon API failure handling
8. Admin authentication protection (require_admin)
9. Credential shielding (no tokens in responses/errors)
"""
from __future__ import annotations

import httpx
import pytest
from fastapi import HTTPException

from app.adapters.github_pr_reader import GitHubPrInfo, GitHubPrReader
from app.adapters.neon_branch_reader import (
    NeonAuthError,
    NeonBranchInfo,
    NeonBranchReader,
    NeonConfigError,
)
from app.adapters.vercel_deployment_reader import (
    VercelDeploymentInfo,
    VercelDeploymentReader,
)
from app.api.v1.endpoints.branch_cleanup import (
    ManualBranchAnalysisRequest,
    analyze_manual_branches,
    get_branch_cleanup_report,
)
from app.api.v1.endpoints.branch_cleanup import (
    router as branch_cleanup_router,
)
from app.config import settings
from app.security import require_admin
from app.services.neon_branch_cleanup_advisor import (
    BranchClassification,
    NeonBranchCleanupAdvisor,
)


# ==============================================================================
# Test 1: 'main' Branch Protected
# ==============================================================================
def test_1_main_branch_protected():
    advisor = NeonBranchCleanupAdvisor()
    branch = NeonBranchInfo(
        branch_id="br-main-001",
        name="main",
        is_default=False,
    )
    pr_info = GitHubPrInfo(state="UNKNOWN")
    vercel_info = VercelDeploymentInfo(state="NOT_FOUND")

    result = advisor.classify_branch(branch, pr_info, vercel_info, data_preservation_verified=False)

    assert result.classification == BranchClassification.PROTECTED
    assert "protected_name" in result.reasons
    assert result.confidence == "verified"


# ==============================================================================
# Test 2: Backup Branch Protected
# ==============================================================================
def test_2_backup_branch_protected():
    advisor = NeonBranchCleanupAdvisor()
    branch = NeonBranchInfo(
        branch_id="br-bak-002",
        name="backup-20261115-pre-migration",
        is_default=False,
    )
    pr_info = GitHubPrInfo(state="CLOSED")
    vercel_info = VercelDeploymentInfo(state="NOT_FOUND")

    result = advisor.classify_branch(branch, pr_info, vercel_info, data_preservation_verified=False)

    assert result.classification == BranchClassification.PROTECTED
    assert "backup_branch" in result.reasons
    assert result.confidence == "verified"


# ==============================================================================
# Test 3: OPEN PR Linked Branch Protected
# ==============================================================================
def test_3_open_pr_branch_protected():
    advisor = NeonBranchCleanupAdvisor()
    branch = NeonBranchInfo(
        branch_id="br-feat-003",
        name="preview/pr-10-core-flow",
        is_default=False,
    )
    pr_info = GitHubPrInfo(pr_number=10, state="OPEN")
    vercel_info = VercelDeploymentInfo(state="ACTIVE_PREVIEW")

    result = advisor.classify_branch(branch, pr_info, vercel_info, data_preservation_verified=False)

    assert result.classification == BranchClassification.PROTECTED
    assert "open_pull_request" in result.reasons
    assert result.confidence == "verified"


# ==============================================================================
# Test 4: Closed/Merged PR With Unverified DB Data -> REVIEW_REQUIRED
# ==============================================================================
def test_4_closed_pr_unverified_db_data_review_required():
    advisor = NeonBranchCleanupAdvisor()
    branch = NeonBranchInfo(
        branch_id="br-feat-004",
        name="preview/pr-8-temp-test",
        is_default=False,
        has_active_endpoint=False,
    )
    pr_info = GitHubPrInfo(pr_number=8, state="MERGED")
    vercel_info = VercelDeploymentInfo(state="PAST_PREVIEW")

    # Data preservation NOT verified (False)
    result = advisor.classify_branch(branch, pr_info, vercel_info, data_preservation_verified=False)

    assert result.classification == BranchClassification.REVIEW_REQUIRED
    assert "data_preservation_not_verified" in result.reasons
    assert result.confidence == "incomplete"


# ==============================================================================
# Test 5: Insufficient Linking Info -> REVIEW_REQUIRED
# ==============================================================================
def test_5_insufficient_linking_info_review_required():
    advisor = NeonBranchCleanupAdvisor()
    branch = NeonBranchInfo(
        branch_id="br-orphan-005",
        name="preview/orphan-experiment",
        is_default=False,
        has_active_endpoint=False,
    )
    # PR state is UNKNOWN / NOT_FOUND
    pr_info = GitHubPrInfo(state="UNKNOWN")
    vercel_info = VercelDeploymentInfo(state="UNCONFIRMED_OR_DELETED")

    result = advisor.classify_branch(branch, pr_info, vercel_info, data_preservation_verified=True)

    assert result.classification == BranchClassification.REVIEW_REQUIRED
    assert "unresolved_pr_relationship" in result.reasons
    assert result.confidence == "incomplete"


# ==============================================================================
# Test 6: All Conditions Verified -> DELETE_CANDIDATE
# ==============================================================================
def test_6_all_conditions_verified_delete_candidate():
    advisor = NeonBranchCleanupAdvisor()
    branch = NeonBranchInfo(
        branch_id="br-clean-006",
        name="preview/pr-7-old-feature",
        is_default=False,
        has_active_endpoint=False,
    )
    pr_info = GitHubPrInfo(pr_number=7, state="MERGED")
    vercel_info = VercelDeploymentInfo(state="PAST_PREVIEW")

    # Explicitly verified that no unmerged/unique data exists on this branch
    result = advisor.classify_branch(branch, pr_info, vercel_info, data_preservation_verified=True)

    assert result.classification == BranchClassification.DELETE_CANDIDATE
    assert "pr_merged" in result.reasons
    assert "no_active_deployment" in result.reasons
    assert "no_active_compute" in result.reasons
    assert "data_preservation_verified" in result.reasons
    assert result.confidence == "verified"


# ==============================================================================
# Test 7: Neon API Failure Handling
# ==============================================================================
def test_7_neon_api_failure_handling():
    # 7a. Missing config raises NeonConfigError
    unconfigured_reader = NeonBranchReader(api_key="", project_id="")
    with pytest.raises(NeonConfigError, match="not configured"):
        unconfigured_reader.fetch_branches()

    # 7b. HTTP 401 error handling
    def mock_401_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Invalid token"}, request=request)

    client_401 = httpx.Client(transport=httpx.MockTransport(mock_401_handler))
    reader_401 = NeonBranchReader(
        api_key="bad_key", project_id="proj_123", client=client_401
    )
    with pytest.raises(NeonAuthError, match="authentication rejected"):
        reader_401.fetch_branches()

    # 7c. Endpoint exception mapping
    with pytest.raises(HTTPException) as exc_503:
        advisor = NeonBranchCleanupAdvisor(neon_reader=unconfigured_reader)
        get_branch_cleanup_report(advisor=advisor)
    assert exc_503.value.status_code == 503


# ==============================================================================
# Test 8: Admin Authentication Protection (require_admin)
# ==============================================================================
def test_8_admin_auth_endpoints(monkeypatch):
    # 1. Route dependencies verification
    routes_by_path = {r.path: r for r in branch_cleanup_router.routes}
    assert "/admin/branch-cleanup/report" in routes_by_path
    assert "/admin/branch-cleanup/analyze" in routes_by_path

    # Verify router requires require_admin
    assert any(d.dependency == require_admin for d in branch_cleanup_router.dependencies)

    # 2. Verify security barrier logic
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "ADMIN_API_KEY", "super_secret_admin_key")

    with pytest.raises(HTTPException) as exc_missing:
        require_admin(None)
    assert exc_missing.value.status_code == 401

    with pytest.raises(HTTPException) as exc_invalid:
        require_admin("wrong_key")
    assert exc_invalid.value.status_code == 401

    # Valid key passes
    require_admin("super_secret_admin_key")


# ==============================================================================
# Test 9: API Credential Shielding (No Secrets in Outputs/Errors)
# ==============================================================================
def test_9_credential_shielding():
    secret_neon_key = "NEON_SECRET_KEY_99999"
    secret_gh_token = "GH_SECRET_TOKEN_88888"
    secret_vercel_token = "VERCEL_SECRET_TOKEN_77777"

    # Mock Neon response with 1 branch
    def mock_neon_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "branches": [
                    {
                        "id": "br-pr-9-test",
                        "name": "preview/pr-9",
                        "current_state": "ready",
                        "default": False,
                    }
                ]
            },
            request=request,
        )

    neon_reader = NeonBranchReader(
        api_key=secret_neon_key,
        project_id="proj_xyz",
        client=httpx.Client(transport=httpx.MockTransport(mock_neon_handler)),
    )
    gh_reader = GitHubPrReader(token=secret_gh_token)
    vercel_reader = VercelDeploymentReader(token=secret_vercel_token)

    advisor = NeonBranchCleanupAdvisor(
        neon_reader=neon_reader,
        github_reader=gh_reader,
        vercel_reader=vercel_reader,
    )

    report = advisor.generate_report()
    report_json = report.model_dump_json()

    assert secret_neon_key not in report_json
    assert secret_gh_token not in report_json
    assert secret_vercel_token not in report_json

    # Test manual analyze endpoint preserves credential shielding
    manual_req = ManualBranchAnalysisRequest(
        branches=[{"id": "br-manual-1", "name": "preview/pr-1"}],
        verified_branches=[],
    )
    manual_res = analyze_manual_branches(manual_req, advisor=advisor)
    manual_json = manual_res.model_dump_json()

    assert secret_neon_key not in manual_json
    assert secret_gh_token not in manual_json
    assert secret_vercel_token not in manual_json
