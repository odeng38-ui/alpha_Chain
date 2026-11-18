"""GitHub Pull Request Reader Adapter for Branch Cleanup Advisor.

Read-only inspection of PR statuses corresponding to preview branches:
- Identifies matching PR by branch naming conventions or head ref
- Discovers PR state: OPEN, MERGED, CLOSED, NOT_FOUND, UNKNOWN
- Never modifies or comments on PRs
"""
from __future__ import annotations

import logging
import re
from typing import Optional

import httpx
from pydantic import BaseModel

from app.config import settings

logger = logging.getLogger(__name__)


class GitHubPrInfo(BaseModel):
    """PR status details resolved from GitHub."""

    pr_number: Optional[int] = None
    state: str = "UNKNOWN"  # "OPEN", "MERGED", "CLOSED", "NOT_FOUND", "UNKNOWN"
    title: Optional[str] = None
    head_ref: Optional[str] = None
    merged_at: Optional[str] = None
    closed_at: Optional[str] = None


class GitHubPrReader:
    """Read-only GitHub API client for checking PR lifecycle states."""

    def __init__(
        self,
        token: Optional[str] = None,
        repository: Optional[str] = None,
        client: Optional[httpx.Client] = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.token = (token if token is not None else settings.GITHUB_TOKEN).strip()
        self.repository = (
            repository if repository is not None else settings.GITHUB_REPOSITORY
        ).strip()
        self.client = client
        self.timeout_seconds = timeout_seconds
        self.base_url = "https://api.github.com"

    def _get_client(self) -> httpx.Client:
        if self.client is not None:
            return self.client
        return httpx.Client(timeout=self.timeout_seconds)

    def extract_pr_number_from_branch(self, branch_name: str) -> Optional[int]:
        """Extract PR number from common branch naming conventions."""
        # e.g., preview/pr-10, preview/pr-10-core-flow, pr-10, pull/10, preview-10
        patterns = [
            r"(?:preview/)?(?:pr|pull)[-_/](\d+)",
            r"^preview-(\d+)",
            r"[-_/]pr(\d+)(?:[-_/]|$)",
        ]
        for pat in patterns:
            m = re.search(pat, branch_name, re.IGNORECASE)
            if m:
                try:
                    return int(m.group(1))
                except ValueError:
                    pass
        return None

    def get_pr_status_for_branch(self, branch_name: str) -> GitHubPrInfo:
        """Query GitHub API to determine the state of the PR for a given branch."""
        pr_number = self.extract_pr_number_from_branch(branch_name)

        if not pr_number:
            # Cannot infer PR number safely from branch name
            return GitHubPrInfo(pr_number=None, state="UNKNOWN")

        if not self.repository:
            return GitHubPrInfo(pr_number=pr_number, state="UNKNOWN")

        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "alpha-chain-branch-cleanup",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        url = f"{self.base_url}/repos/{self.repository}/pulls/{pr_number}"
        client = self._get_client()

        try:
            if self.client is not None:
                res = client.get(url, headers=headers)
            else:
                with client:
                    res = client.get(url, headers=headers)
        except Exception as exc:
            logger.warning("Error querying GitHub PR %s: %s", pr_number, exc)
            return GitHubPrInfo(pr_number=pr_number, state="UNKNOWN")

        if res.status_code == 404:
            return GitHubPrInfo(pr_number=pr_number, state="NOT_FOUND")

        if res.status_code != 200:
            logger.warning("GitHub API returned HTTP %s for PR %s", res.status_code, pr_number)
            return GitHubPrInfo(pr_number=pr_number, state="UNKNOWN")

        try:
            data = res.json()
        except Exception:
            return GitHubPrInfo(pr_number=pr_number, state="UNKNOWN")

        raw_state = str(data.get("state", "")).lower()
        merged_at = data.get("merged_at")
        closed_at = data.get("closed_at")

        if merged_at:
            state = "MERGED"
        elif raw_state == "closed":
            state = "CLOSED"
        elif raw_state == "open":
            state = "OPEN"
        else:
            state = "UNKNOWN"

        head_ref = None
        head_obj = data.get("head")
        if isinstance(head_obj, dict):
            head_ref = head_obj.get("ref")

        return GitHubPrInfo(
            pr_number=pr_number,
            state=state,
            title=data.get("title"),
            head_ref=head_ref,
            merged_at=merged_at,
            closed_at=closed_at,
        )
