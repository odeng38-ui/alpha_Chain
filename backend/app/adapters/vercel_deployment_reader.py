"""Vercel Deployment Reader Adapter for Branch Cleanup Advisor.

Read-only inspection of Vercel deployments associated with preview environments:
- Categorizes deployments: ACTIVE_PREVIEW, PAST_PREVIEW, FAILED_PREVIEW, UNCONFIRMED_OR_DELETED, NOT_FOUND
- Never triggers, creates, or deletes deployments
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx
from pydantic import BaseModel

from app.config import settings

logger = logging.getLogger(__name__)


class VercelDeploymentInfo(BaseModel):
    """Vercel deployment status metadata."""

    deployment_id: Optional[str] = None
    state: str = "UNCONFIRMED_OR_DELETED"  # "ACTIVE_PREVIEW", "PAST_PREVIEW", "FAILED_PREVIEW", "UNCONFIRMED_OR_DELETED", "NOT_FOUND"
    url: Optional[str] = None
    target: Optional[str] = None  # "preview", "production"
    created_at: Optional[str] = None


class VercelDeploymentReader:
    """Read-only client for inspecting Vercel preview deployment statuses."""

    def __init__(
        self,
        token: Optional[str] = None,
        project_id: Optional[str] = None,
        client: Optional[httpx.Client] = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.token = (token if token is not None else settings.VERCEL_TOKEN).strip()
        self.project_id = (
            project_id if project_id is not None else settings.VERCEL_PROJECT_ID
        ).strip()
        self.client = client
        self.timeout_seconds = timeout_seconds
        self.base_url = "https://api.vercel.com"

    def _get_client(self) -> httpx.Client:
        if self.client is not None:
            return self.client
        return httpx.Client(timeout=self.timeout_seconds)

    def get_deployment_status_for_branch(
        self,
        branch_name: str,
        pr_number: Optional[int] = None,
    ) -> VercelDeploymentInfo:
        """Query Vercel deployments API and determine preview state."""
        if not self.token or not self.project_id:
            return VercelDeploymentInfo(
                state="UNCONFIRMED_OR_DELETED",
            )

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }
        url = f"{self.base_url}/v6/deployments?projectId={self.project_id}&limit=50"
        client = self._get_client()

        try:
            if self.client is not None:
                res = client.get(url, headers=headers)
            else:
                with client:
                    res = client.get(url, headers=headers)
        except Exception as exc:
            logger.warning("Error querying Vercel deployments: %s", exc)
            return VercelDeploymentInfo(state="UNCONFIRMED_OR_DELETED")

        if res.status_code != 200:
            logger.warning("Vercel API returned HTTP %s", res.status_code)
            return VercelDeploymentInfo(state="UNCONFIRMED_OR_DELETED")

        try:
            data = res.json()
        except Exception:
            return VercelDeploymentInfo(state="UNCONFIRMED_OR_DELETED")

        deployments = data.get("deployments", [])
        if not isinstance(deployments, list):
            return VercelDeploymentInfo(state="UNCONFIRMED_OR_DELETED")

        # Match deployments to branch or PR
        matches: list[dict[str, Any]] = []
        for dep in deployments:
            meta = dep.get("meta") or {}
            dep_ref = str(meta.get("githubCommitRef") or "").strip()
            dep_pr = str(meta.get("githubPrId") or "").strip()

            if dep_ref == branch_name:
                matches.append(dep)
            elif pr_number and dep_pr == str(pr_number):
                matches.append(dep)

        if not matches:
            return VercelDeploymentInfo(state="NOT_FOUND")

        # Sort newest first by created (timestamp in ms or date string)
        matches.sort(key=lambda d: d.get("created", 0), reverse=True)
        latest = matches[0]

        dep_state = str(latest.get("state") or "").upper()
        target = str(latest.get("target") or "preview").lower()
        dep_id = latest.get("uid") or latest.get("id")
        url_str = latest.get("url")
        created_str = str(latest.get("created", ""))

        if dep_state in ("ERROR", "CANCELED"):
            return VercelDeploymentInfo(
                deployment_id=dep_id,
                state="FAILED_PREVIEW",
                url=url_str,
                target=target,
                created_at=created_str,
            )

        if dep_state == "READY":
            # If there are multiple matches and this is the latest one, it's ACTIVE_PREVIEW
            return VercelDeploymentInfo(
                deployment_id=dep_id,
                state="ACTIVE_PREVIEW",
                url=url_str,
                target=target,
                created_at=created_str,
            )

        return VercelDeploymentInfo(
            deployment_id=dep_id,
            state="PAST_PREVIEW",
            url=url_str,
            target=target,
            created_at=created_str,
        )
