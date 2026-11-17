"""SEC EDGAR official filing data adapter.

Connects to SEC EDGAR official Submissions API to retrieve corporate regulatory filings.
Strict compliance with SEC fair access policies:
- Explicit, user-identifying User-Agent header (format: 'Sample Company Name AdminContact@<sample company domain>.com')
- Rate-limiting (default <= 10 requests per second)
- Exponential backoff on HTTP 429 rate limit errors
- Isolated testable HTTP client via httpx
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


class SecAdapterError(RuntimeError):
    """Base exception for SEC EDGAR adapter errors."""

    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class SecAuthError(SecAdapterError):
    """User-Agent rejected or unauthorized (HTTP 401/403)."""

    pass


class SecRateLimitError(SecAdapterError):
    """Rate limit exceeded by SEC EDGAR (HTTP 429)."""

    pass


class SecNotFoundError(SecAdapterError):
    """CIK or resource not found on SEC EDGAR (HTTP 404)."""

    pass


def validate_sec_user_agent(ua: str) -> str:
    """Validate that User-Agent satisfies SEC fair access rules with real contact info."""
    cleaned = (ua or "").strip()
    if not cleaned:
        raise ValueError("SEC_USER_AGENT is required for SEC EDGAR fair access compliance")
    lower = cleaned.lower()
    if "alphachain.local" in lower or ".local" in lower or "example.com" in lower:
        raise ValueError(
            "Fake or dummy User-Agent (.local/example.com) is rejected. "
            "SEC fair access requires a real contactable entity and email (e.g., 'AlphaChain/1.0 admin@alphachain.org')."
        )
    if "@" not in cleaned and not any(p in lower for p in ("http://", "https://")):
        raise ValueError(
            "SEC fair access policy requires contact information (email or URL) in User-Agent header."
        )
    return cleaned


class SecEdgarAdapter:
    """Official SEC EDGAR Submissions API client."""

    BASE_URL = "https://data.sec.gov"

    def __init__(
        self,
        user_agent: Optional[str] = None,
        *,
        timeout: float = 10.0,
        rate_limit_per_sec: float = 10.0,
        max_retries: int = 3,
        client: Optional[httpx.Client] = None,
    ) -> None:
        from app.config import settings

        raw_ua = user_agent or getattr(settings, "SEC_USER_AGENT", "AlphaChain/1.0 (contact@alphachain.org)")
        self.user_agent = validate_sec_user_agent(raw_ua)

        self.timeout = timeout
        self.rate_limit_per_sec = max(0.1, rate_limit_per_sec)
        self.min_interval = 1.0 / self.rate_limit_per_sec
        self.max_retries = max_retries
        self._last_request_time: float = 0.0

        self._external_client = client is not None
        self.client = client or httpx.Client(timeout=timeout)

    def close(self) -> None:
        if not self._external_client:
            self.client.close()

    def __enter__(self) -> SecEdgarAdapter:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def _throttle(self) -> None:
        """Enforce SEC 10 requests per second fair access policy."""
        now = time.monotonic()
        elapsed = now - self._last_request_time
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_request_time = time.monotonic()

    def _request(self, path: str) -> dict[str, Any]:
        """Send bounded GET request with retry and rate-limiting to SEC EDGAR."""
        headers = {
            "User-Agent": self.user_agent,
            "Accept-Encoding": "gzip, deflate",
            "Accept": "application/json",
        }
        url = f"{self.BASE_URL}/{path.lstrip('/')}"
        last_error: Optional[Exception] = None

        for attempt in range(self.max_retries):
            self._throttle()
            try:
                response = self.client.get(url, headers=headers)
                if response.status_code == 200:
                    return response.json()
                elif response.status_code in (401, 403):
                    raise SecAuthError(
                        f"SEC EDGAR access rejected ({response.status_code}): Check User-Agent header '{self.user_agent}'",
                        status_code=response.status_code,
                    )
                elif response.status_code == 404:
                    raise SecNotFoundError(
                        f"Resource not found on SEC EDGAR: {url}",
                        status_code=404,
                    )
                elif response.status_code == 429:
                    retry_after = float(response.headers.get("Retry-After", 1.0 * (2**attempt)))
                    logger.warning("SEC EDGAR 429 rate limit encountered; waiting %.2fs", retry_after)
                    time.sleep(retry_after)
                    continue
                elif 500 <= response.status_code < 600:
                    # Transient server error
                    time.sleep(0.5 * (2**attempt))
                    continue
                else:
                    response.raise_for_status()
            except (SecAuthError, SecNotFoundError):
                raise
            except Exception as exc:
                last_error = exc
                logger.warning("SEC request attempt %d failed: %s", attempt + 1, exc)
                time.sleep(0.5 * (2**attempt))

        raise SecAdapterError(f"SEC EDGAR request failed after {self.max_retries} attempts: {last_error}")

    def get_submissions(self, cik: str) -> dict[str, Any]:
        """Fetch company submission history by CIK."""
        from app.services.identifier_service import normalize_identifier

        norm_cik = normalize_identifier("CIK", cik)
        path = f"submissions/CIK{norm_cik}.json"
        return self._request(path)

    def get_recent_filings(
        self,
        cik: str,
        *,
        form_types: Optional[set[str]] = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Retrieve and normalize recent filings from SEC submissions metadata.

        Returns normalized records containing accession_number, form, published_at,
        source_url, primary_doc, and filing titles.
        """
        from app.services.identifier_service import normalize_identifier

        norm_cik = normalize_identifier("CIK", cik)
        data = self.get_submissions(norm_cik)

        entity_name = data.get("name", "")
        tickers = data.get("tickers", [])
        symbol = tickers[0] if tickers else None

        filings_block = data.get("filings", {}).get("recent", {})
        if not filings_block:
            return []

        accession_numbers = filings_block.get("accessionNumber", [])
        forms = filings_block.get("form", [])
        filing_dates = filings_block.get("filingDate", [])
        acceptance_datetimes = filings_block.get("acceptanceDateTime", [])
        primary_docs = filings_block.get("primaryDocument", [])
        doc_descriptions = filings_block.get("primaryDocDescription", [])
        items_list = filings_block.get("items", [])

        normalized: list[dict[str, Any]] = []
        cik_int = int(norm_cik)

        for i in range(len(accession_numbers)):
            if len(normalized) >= limit:
                break

            form = forms[i] if i < len(forms) else ""
            if form_types and form not in form_types:
                continue

            acc_num = accession_numbers[i]
            acc_nodash = acc_num.replace("-", "")
            p_doc = primary_docs[i] if i < len(primary_docs) else "document.htm"
            source_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/{p_doc}"

            # Parse published_at timestamp
            pub_at: Optional[datetime] = None
            if i < len(acceptance_datetimes) and acceptance_datetimes[i]:
                try:
                    dt_str = acceptance_datetimes[i].replace("Z", "+00:00")
                    pub_at = datetime.fromisoformat(dt_str).astimezone(timezone.utc)
                except Exception:
                    pass

            if pub_at is None and i < len(filing_dates) and filing_dates[i]:
                try:
                    pub_at = datetime.strptime(filing_dates[i], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                except Exception:
                    pass

            doc_desc = doc_descriptions[i] if i < len(doc_descriptions) else ""
            item_code = items_list[i] if i < len(items_list) else ""
            title = f"{entity_name} SEC Form {form} (Item {item_code})" if item_code else f"{entity_name} SEC Form {form}"

            normalized.append(
                {
                    "cik": norm_cik,
                    "company_name": entity_name,
                    "symbol": symbol,
                    "accession_number": acc_num,
                    "form": form,
                    "published_at": pub_at,
                    "source_url": source_url,
                    "primary_document": p_doc,
                    "title": title,
                    "summary": f"{title} - {doc_desc}".strip(" -"),
                    "excerpt": f"Official SEC Form {form} filing {acc_num} submitted on {filing_dates[i] if i < len(filing_dates) else ''}",
                }
            )

        return normalized

