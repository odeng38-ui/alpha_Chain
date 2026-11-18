"""Neon API Branch Reader Adapter.

Read-only client for retrieving Neon PostgreSQL project branches and endpoints.
Adheres strictly to ALPHA CHAIN safety rules:
- Read-only operations only (NO branch create, update, or delete)
- Safe exception handling without leaking tokens or project secrets
- Offline/manual mode fallback support
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx
from pydantic import BaseModel, Field

from app.config import settings

logger = logging.getLogger(__name__)


class NeonReaderError(Exception):
    """Base exception for Neon reader errors."""

    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class NeonConfigError(NeonReaderError):
    """Missing or invalid Neon API configuration."""

    pass


class NeonAuthError(NeonReaderError):
    """Neon API authentication failure (HTTP 401/403)."""

    pass


class NeonRateLimitError(NeonReaderError):
    """Neon API rate limit exceeded (HTTP 429)."""

    pass


class NeonNotFoundError(NeonReaderError):
    """Neon project or branch not found (HTTP 404)."""

    pass


class NeonComputeEndpoint(BaseModel):
    """Information about a compute endpoint attached to a Neon branch."""

    endpoint_id: str
    current_state: str = "idle"  # "idle", "active", "init"
    autoscaling_limit_min_cu: Optional[float] = None
    autoscaling_limit_max_cu: Optional[float] = None


class NeonBranchInfo(BaseModel):
    """Standardized metadata for a Neon database branch."""

    branch_id: str
    name: str
    parent_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    is_default: bool = False
    current_state: str = "ready"
    logical_size: Optional[int] = None
    has_active_endpoint: bool = False
    endpoints: list[NeonComputeEndpoint] = Field(default_factory=list)


class NeonBranchReader:
    """Read-only client for Neon Project API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        project_id: Optional[str] = None,
        client: Optional[httpx.Client] = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.api_key = (api_key if api_key is not None else settings.NEON_API_KEY).strip()
        self.project_id = (
            project_id if project_id is not None else settings.NEON_PROJECT_ID
        ).strip()
        self.client = client
        self.timeout_seconds = timeout_seconds
        self.base_url = "https://console.neon.tech/api/v2"

    def _get_client(self) -> httpx.Client:
        if self.client is not None:
            return self.client
        return httpx.Client(timeout=self.timeout_seconds)

    def _validate_config(self) -> None:
        if not self.api_key:
            raise NeonConfigError(
                "Neon API authentication key (NEON_API_KEY) is not configured."
            )
        if not self.project_id:
            raise NeonConfigError(
                "Neon Project ID (NEON_PROJECT_ID) is not configured."
            )

    def fetch_branches(self) -> list[NeonBranchInfo]:
        """Fetch project branches and associated compute endpoints via Neon official API."""
        self._validate_config()
        client = self._get_client()

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        # 1. Fetch branches
        branches_url = f"{self.base_url}/projects/{self.project_id}/branches"
        try:
            if self.client is not None:
                res = client.get(branches_url, headers=headers)
            else:
                with client:
                    res = client.get(branches_url, headers=headers)
        except httpx.TimeoutException as exc:
            raise NeonReaderError("Neon API request timed out", status_code=504) from exc
        except httpx.RequestError as exc:
            raise NeonReaderError(
                f"Neon API connection failed: {type(exc).__name__}", status_code=502
            ) from exc

        if res.status_code in (401, 403):
            raise NeonAuthError(
                "Neon API authentication rejected. Please verify your NEON_API_KEY.",
                status_code=res.status_code,
            )
        if res.status_code == 404:
            raise NeonNotFoundError(
                f"Neon project '{self.project_id}' not found.", status_code=404
            )
        if res.status_code == 429:
            raise NeonRateLimitError(
                "Neon API rate limit exceeded.", status_code=429
            )
        if res.status_code >= 500:
            raise NeonReaderError(
                f"Neon API server error: HTTP {res.status_code}", status_code=res.status_code
            )

        try:
            data = res.json()
        except Exception as exc:
            raise NeonReaderError("Invalid JSON returned from Neon API", status_code=502) from exc

        raw_branches = data.get("branches", [])
        if not isinstance(raw_branches, list):
            raise NeonReaderError("Malformed Neon API response: 'branches' is not a list")

        # 2. Try fetching endpoints to check for active compute
        endpoints_by_branch: dict[str, list[NeonComputeEndpoint]] = {}
        try:
            endpoints_url = f"{self.base_url}/projects/{self.project_id}/endpoints"
            if self.client is not None:
                ep_res = client.get(endpoints_url, headers=headers)
            else:
                with self._get_client() as c:
                    ep_res = c.get(endpoints_url, headers=headers)
            if ep_res.status_code == 200:
                for ep in ep_res.json().get("endpoints", []):
                    b_id = ep.get("branch_id")
                    if b_id:
                        endpoint_obj = NeonComputeEndpoint(
                            endpoint_id=ep.get("id", ""),
                            current_state=ep.get("current_state", "idle"),
                            autoscaling_limit_min_cu=ep.get("autoscaling_limit_min_cu"),
                            autoscaling_limit_max_cu=ep.get("autoscaling_limit_max_cu"),
                        )
                        endpoints_by_branch.setdefault(b_id, []).append(endpoint_obj)
        except Exception as exc:
            logger.warning("Could not retrieve Neon endpoints, continuing with branch data: %s", exc)

        return self._normalize_branches(raw_branches, endpoints_by_branch)

    def parse_manual_branches(self, data: Any) -> list[NeonBranchInfo]:
        """Parse manually provided branch data (e.g. from JSON file or request body)."""
        if isinstance(data, dict):
            raw_branches = data.get("branches", [])
        elif isinstance(data, list):
            raw_branches = data
        else:
            raise ValueError("Input data must be a dictionary with 'branches' or a list of branch objects")

        return self._normalize_branches(raw_branches, {})

    def _normalize_branches(
        self,
        raw_branches: list[dict[str, Any]],
        endpoints_by_branch: dict[str, list[NeonComputeEndpoint]],
    ) -> list[NeonBranchInfo]:
        results: list[NeonBranchInfo] = []
        for b in raw_branches:
            branch_id = str(b.get("id") or b.get("branch_id") or "").strip()
            name = str(b.get("name") or "").strip()
            if not branch_id or not name:
                continue

            eps = endpoints_by_branch.get(branch_id, [])
            # Also check if endpoints was passed in raw dict
            if not eps and "endpoints" in b:
                for raw_ep in b.get("endpoints", []):
                    eps.append(
                        NeonComputeEndpoint(
                            endpoint_id=raw_ep.get("id", raw_ep.get("endpoint_id", "")),
                            current_state=raw_ep.get("current_state", "idle"),
                        )
                    )

            has_active = any(ep.current_state in ("active", "init") for ep in eps)

            results.append(
                NeonBranchInfo(
                    branch_id=branch_id,
                    name=name,
                    parent_id=b.get("parent_id"),
                    created_at=b.get("created_at"),
                    updated_at=b.get("updated_at"),
                    is_default=bool(b.get("default", b.get("is_default", False))),
                    current_state=b.get("current_state", "ready"),
                    logical_size=b.get("logical_size"),
                    has_active_endpoint=has_active,
                    endpoints=eps,
                )
            )
        return results
