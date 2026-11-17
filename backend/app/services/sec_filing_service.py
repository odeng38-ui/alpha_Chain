"""SEC official regulatory filing ingestion service.

Connects SEC EDGAR official filings to Alpha Chain core infrastructure by delegating
to SecEventAnchorService.ingest_sec_filing().
Maintains strict transaction control (commit/rollback) and supports dry-run mode.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.adapters.sec_adapter import SecEdgarAdapter
from app.services.event_evidence_service import SecEventAnchorService
from app.services.identifier_service import normalize_identifier

logger = logging.getLogger(__name__)


class SecFilingValidationError(ValueError):
    """Validation failure on incoming SEC filing payload."""

    pass


class SecFilingService:
    """Orchestrates single-filing and CIK-based official SEC filing ingestion."""

    @staticmethod
    def validate_filing_payload(payload: dict[str, Any]) -> dict[str, Any]:
        """Validate and normalize filing payload fields."""
        cik = str(payload.get("cik") or "").strip()
        if not cik:
            raise SecFilingValidationError("cik is required")

        norm_cik = normalize_identifier("CIK", cik)

        acc = str(payload.get("accession_number") or "").strip()
        if not acc:
            raise SecFilingValidationError("accession_number is required")

        source_url = str(payload.get("source_url") or "").strip()
        if not source_url:
            raise SecFilingValidationError("source_url is required")
        if not (source_url.startswith("https://www.sec.gov/") or source_url.startswith("https://data.sec.gov/")):
            raise SecFilingValidationError(f"Invalid official SEC source_url domain: {source_url}")

        published_at = payload.get("published_at")
        if published_at is None:
            raise SecFilingValidationError("published_at is required")

        if isinstance(published_at, str):
            try:
                published_at = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
            except Exception as exc:
                raise SecFilingValidationError(f"Invalid published_at format: {exc}")

        if not isinstance(published_at, datetime):
            raise SecFilingValidationError("published_at must be a datetime")

        # Ensure timezone-aware UTC
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)
        else:
            published_at = published_at.astimezone(timezone.utc)

        # Disallow future dates
        now = datetime.now(timezone.utc)
        if published_at > now:
            raise SecFilingValidationError(f"published_at cannot be in the future (published_at={published_at.isoformat()}, now={now.isoformat()})")

        title = str(payload.get("title") or f"SEC Form {payload.get('form', 'Filing')} {acc}").strip()
        summary = payload.get("summary")
        excerpt = payload.get("excerpt") or summary or title
        event_kind = str(payload.get("event_kind") or "CAPEX_EXPANSION").strip()
        symbol = payload.get("symbol")

        return {
            "cik": norm_cik,
            "accession_number": acc,
            "title": title,
            "published_at": published_at,
            "source_url": source_url,
            "summary": summary,
            "excerpt": excerpt,
            "event_kind": event_kind,
            "symbol": symbol,
            "form": payload.get("form"),
        }

    @classmethod
    def ingest_filing(
        cls,
        db: Session,
        *,
        filing_data: dict[str, Any],
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Ingest a validated SEC filing into GlobalEvent, GlobalEventEvidence, and EventCompanyLink.

        If dry_run=True, performs validation and identifier mapping checks without persisting to the database.
        """
        validated = cls.validate_filing_payload(filing_data)

        if dry_run:
            # Perform read-only preview by rolling back any flush
            try:
                result = SecEventAnchorService.ingest_sec_filing(
                    db,
                    cik=validated["cik"],
                    accession_number=validated["accession_number"],
                    title=validated["title"],
                    published_at=validated["published_at"],
                    source_url=validated["source_url"],
                    summary=validated["summary"],
                    excerpt=validated["excerpt"],
                    event_kind=validated["event_kind"],
                    symbol=validated["symbol"],
                )
                db.rollback()
                result["dry_run"] = True
                result["persisted"] = False
                return result
            except Exception:
                db.rollback()
                raise

        # Real persistent ingestion with explicit transaction boundary
        try:
            result = SecEventAnchorService.ingest_sec_filing(
                db,
                cik=validated["cik"],
                accession_number=validated["accession_number"],
                title=validated["title"],
                published_at=validated["published_at"],
                source_url=validated["source_url"],
                summary=validated["summary"],
                excerpt=validated["excerpt"],
                event_kind=validated["event_kind"],
                symbol=validated["symbol"],
            )
            db.commit()
            result["dry_run"] = False
            result["persisted"] = True
            return result
        except Exception:
            db.rollback()
            raise

    @classmethod
    def ingest_latest_filing_by_cik(
        cls,
        db: Session,
        *,
        cik: str,
        form_type: str = "8-K",
        dry_run: bool = False,
        adapter: Optional[SecEdgarAdapter] = None,
        event_kind: str = "CAPEX_EXPANSION",
    ) -> dict[str, Any]:
        """Fetch the most recent filing of the given form_type for a CIK and ingest it."""
        from app.services.identifier_service import normalize_identifier

        norm_cik = normalize_identifier("CIK", cik)
        adapter_provided = adapter is not None
        sec_adapter = adapter or SecEdgarAdapter()

        try:
            filings = sec_adapter.get_recent_filings(
                norm_cik,
                form_types={form_type} if form_type else None,
                limit=1,
            )
            if not filings:
                return {
                    "status": "not_found",
                    "cik": norm_cik,
                    "form_type": form_type,
                    "reason": f"No recent Form {form_type} filings found for CIK={norm_cik}",
                    "dry_run": dry_run,
                    "persisted": False,
                }

            filing = filings[0]
            filing["event_kind"] = event_kind
            return cls.ingest_filing(db, filing_data=filing, dry_run=dry_run)
        finally:
            if not adapter_provided:
                sec_adapter.close()
