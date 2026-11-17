"""SEC official regulatory filing ingestion service.

Connects SEC EDGAR official filings to Alpha Chain core infrastructure by delegating
to SecEventAnchorService.ingest_sec_filing().
Maintains strict transaction control (commit/rollback) and supports pure read-only dry-run mode.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.adapters.sec_adapter import SecEdgarAdapter
from app.models.schema import Company, GlobalEvent, IdentifierMap
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
        # Do not default all 8-K filings to CAPEX_EXPANSION; default to UNCLASSIFIED_FILING
        event_kind = str(payload.get("event_kind") or "UNCLASSIFIED_FILING").strip()
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
    def preview_filing(cls, db: Session, *, validated: dict[str, Any]) -> dict[str, Any]:
        """Perform a pure read-only preview of identifier binding without adding or flushing rows."""
        external_id = f"sec:{validated['cik']}:{validated['accession_number']}"
        existing_event = db.query(GlobalEvent).filter(GlobalEvent.external_id == external_id).first()

        active_maps = (
            db.query(IdentifierMap)
            .filter(
                IdentifierMap.source == "CIK",
                IdentifierMap.source_id_value == validated["cik"],
                IdentifierMap.effective_to.is_(None),
            )
            .all()
        )

        base_res: dict[str, Any] = {
            "event_id": existing_event.id if existing_event else None,
            "evidence_id": None,
            "anchor_link_id": None,
            "company_id": None,
            "dry_run": True,
            "persisted": False,
            "created_event": existing_event is None,
            "created_evidence": True,
            "title": validated["title"],
            "published_at": validated["published_at"].isoformat(),
            "source_url": validated["source_url"],
            "event_kind": validated["event_kind"],
        }

        if not active_maps:
            base_res.update({
                "status": "hold_no_identifier_mapping",
                "reason": f"No active IdentifierMap found for CIK={validated['cik']}",
            })
            return base_res

        company_ids = {m.company_id for m in active_maps}
        if len(company_ids) > 1:
            base_res.update({
                "status": "hold_identifier_conflict",
                "reason": f"CIK={validated['cik']} maps to multiple companies: {company_ids}",
            })
            return base_res

        company_id = list(company_ids)[0]
        company = db.get(Company, company_id)
        if company is None or company.country_code != "US":
            base_res.update({
                "status": "hold_not_us_company",
                "company_id": company_id,
                "reason": f"Company {company_id} is not a verified US company (country_code={getattr(company, 'country_code', None)})",
            })
            return base_res

        base_res.update({
            "status": "linked_proposed",
            "company_id": company_id,
            "company_name": company.name,
            "verification_status": "proposed",
        })
        return base_res

    @classmethod
    def ingest_filing(
        cls,
        db: Session,
        *,
        filing_data: dict[str, Any],
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Ingest a validated SEC filing.

        If dry_run=True (default), performs pure read-only preview without flushing or adding DB rows.
        If dry_run=False, performs real persistent ingestion with explicit transaction commit.
        """
        validated = cls.validate_filing_payload(filing_data)

        if dry_run:
            # Pure read-only preview (zero writes, zero flushes)
            return cls.preview_filing(db, validated=validated)

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
        dry_run: bool = True,
        adapter: Optional[SecEdgarAdapter] = None,
        event_kind: Optional[str] = None,
    ) -> dict[str, Any]:
        """Fetch the most recent filing of the given form_type for a CIK and ingest/preview it."""
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
            if event_kind:
                filing["event_kind"] = event_kind
            return cls.ingest_filing(db, filing_data=filing, dry_run=dry_run)
        finally:
            if not adapter_provided:
                sec_adapter.close()
