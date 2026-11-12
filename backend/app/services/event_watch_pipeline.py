"""Deterministic Event Watch Pipeline for Alpha Chain (AC-EF-MVP-03C).

Flow:
    Incoming Data Providers (NEWS, DART, SEC, MACRO)
        -> Incremental & Deduplication Filter (Cursor & Hash)
        -> Event Inbox Service (Categorization & Grouping)
        -> Event Router Input Preparation (Ready for Analysis Payloads)

Strict Rules:
- No database writes, no network calls, no scheduler registration.
- Maintain timestamps: published_at, collected_at, as_of (aware UTC).
- Distinguish new vs duplicate vs correction/revision.
- Deliver only 'ready_for_analysis' candidates to router payloads.
- Retain cursor on failures or lack of reliable cursor metadata.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from sqlalchemy.orm import Session

from app.services.dart_service import _base_report_name
from app.services.dart_service import classify_ef_event as classify_dart_event
from app.services.event_inbox_service import (
    CompanyIdentifier,
    EventCandidate,
    EventInboxError,
    EventInboxItem,
    EventInboxService,
    EventType,
    InboxResult,
    InboxStatus,
)
from app.services.event_router_service import EventRouterService

SUPPORTED_WATCH_SOURCES = frozenset({"NEWS", "DART", "SEC", "MACRO", "FRED", "OFFICIAL_WEB"})


def parse_aware_datetime(value: Any) -> datetime:
    """Parse string, date, or datetime to timezone-aware UTC datetime."""
    if isinstance(value, str):
        val_str = value.strip().replace("Z", "+00:00")
        if len(val_str) == 8 and val_str.isdigit():
            # YYYYMMDD format from DART API rcept_dt
            return datetime.strptime(val_str, "%Y%m%d").replace(tzinfo=timezone.utc)
        value = datetime.fromisoformat(val_str)
    elif isinstance(value, datetime):
        pass
    elif hasattr(value, "year") and hasattr(value, "month") and hasattr(value, "day"):
        value = datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    if not isinstance(value, datetime):
        raise ValueError("Timezone-aware datetime required")
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class EventWatchResult:
    """Standardized output contract for EventWatchPipeline."""

    provider_id: str
    source_status: str  # 'connected', 'source_not_connected', 'unsupported_source'
    input_count: int
    new_count: int
    duplicate_count: int
    correction_count: int
    classified_count: int
    needs_review_count: int
    router_ready_count: int
    next_cursor: Any
    exclusion_reasons: tuple[str, ...]
    router_payloads: tuple[dict[str, Any], ...]
    inbox_result: InboxResult | None = None


class EventWatchPipeline:
    """Pure, local, incremental event watch orchestrator."""

    def __init__(
        self,
        inbox_service: EventInboxService | None = None,
        router_service: EventRouterService | None = None,
    ) -> None:
        self.inbox_service = inbox_service or EventInboxService()
        self.router_service = router_service or EventRouterService()

    def process(
        self,
        provider_id: str,
        items: Sequence[Any],
        *,
        as_of: datetime,
        cursor: Any = None,
        max_items: int = 50,
        fail_simulation: bool = False,
    ) -> EventWatchResult:
        """Process incoming raw items from a provider through deduplication and Event Inbox."""
        cutoff = parse_aware_datetime(as_of)
        provider = provider_id.strip().upper()

        # Connection status determination per source specifications
        if provider == "SEC":
            source_status = "source_not_connected"
        elif provider in SUPPORTED_WATCH_SOURCES:
            source_status = "connected"
        else:
            source_status = "unsupported_source"

        # Failure simulation check (e.g. data source failure or unhandled condition)
        if fail_simulation:
            return EventWatchResult(
                provider_id=provider,
                source_status=source_status,
                input_count=len(items),
                new_count=0,
                duplicate_count=0,
                correction_count=0,
                classified_count=0,
                needs_review_count=0,
                router_ready_count=0,
                next_cursor=cursor,  # Retain existing cursor without advancement!
                exclusion_reasons=("PROCESSING_FAILED_CURSOR_HELD",),
                router_payloads=(),
                inbox_result=None,
            )

        if not items:
            reasons = ["SOURCE_NOT_CONNECTED"] if source_status == "source_not_connected" else []
            return EventWatchResult(
                provider_id=provider,
                source_status=source_status,
                input_count=0,
                new_count=0,
                duplicate_count=0,
                correction_count=0,
                classified_count=0,
                needs_review_count=0,
                router_ready_count=0,
                next_cursor=cursor,
                exclusion_reasons=tuple(reasons),
                router_payloads=(),
                inbox_result=None,
            )

        effective_items = list(items)
        exclusion_reasons: list[str] = []
        if source_status == "source_not_connected":
            exclusion_reasons.append("SOURCE_NOT_CONNECTED")

        # Bounded item processing (cost and memory control)
        if len(effective_items) > max_items:
            effective_items = effective_items[:max_items]
            exclusion_reasons.append("BATCH_SIZE_LIMITED")

        cursor_duplicate_count = 0
        inbox_items: list[EventInboxItem] = []
        correction_count = 0
        observed_cursors: list[Any] = []

        for raw_item in effective_items:
            try:
                norm_item, is_corr, item_cursor = self._normalize_item(provider, raw_item, cutoff)
            except (ValueError, EventInboxError) as err:
                exclusion_reasons.append(f"ITEM_FORMAT_ERROR:{err}")
                continue

            # Cursor filtering (if a comparable cursor was provided)
            if cursor is not None and item_cursor is not None:
                if self._is_before_or_at_cursor(item_cursor, cursor):
                    cursor_duplicate_count += 1
                    continue

            if is_corr:
                correction_count += 1

            if item_cursor is not None:
                observed_cursors.append(item_cursor)

            inbox_items.append(norm_item)

        if cursor_duplicate_count > 0:
            exclusion_reasons.append("CURSOR_FILTERED_DUPLICATES")

        # Process through deterministic Event Inbox
        if inbox_items:
            inbox_result = self.inbox_service.process(inbox_items, as_of=cutoff)
        else:
            inbox_result = InboxResult(candidates=(), documents=())

        # Calculate result metrics
        new_count = sum(
            1
            for doc in inbox_result.documents
            if doc.status in (InboxStatus.NEW, InboxStatus.CLASSIFIED, InboxStatus.GROUPED)
        )
        inbox_duplicates = sum(
            1 for doc in inbox_result.documents if doc.status == InboxStatus.DUPLICATE
        )
        total_duplicates = cursor_duplicate_count + inbox_duplicates

        classified_count = sum(
            1
            for cand in inbox_result.candidates
            if cand.event_type not in (EventType.UNCLASSIFIED, EventType.OTHER)
        )
        needs_review_count = sum(
            1 for cand in inbox_result.candidates if cand.status == InboxStatus.NEEDS_REVIEW
        )

        ready_candidates: list[EventCandidate] = [
            cand
            for cand in inbox_result.candidates
            if cand.status == InboxStatus.READY_FOR_ANALYSIS
        ]
        router_ready_count = len(ready_candidates)
        router_payloads = tuple(cand.to_router_payload() for cand in ready_candidates)

        # Record domain exclusions
        if any(doc.status == InboxStatus.IGNORED for doc in inbox_result.documents):
            exclusion_reasons.append("FUTURE_DATA_IGNORED")
        if needs_review_count > 0:
            exclusion_reasons.append("CANDIDATE_NEEDS_REVIEW")

        # Compute next cursor candidate
        next_cursor = self._calculate_next_cursor(provider, cursor, observed_cursors)

        return EventWatchResult(
            provider_id=provider,
            source_status=source_status,
            input_count=len(items),
            new_count=new_count,
            duplicate_count=total_duplicates,
            correction_count=correction_count,
            classified_count=classified_count,
            needs_review_count=needs_review_count,
            router_ready_count=router_ready_count,
            next_cursor=next_cursor,
            exclusion_reasons=tuple(exclusion_reasons),
            router_payloads=router_payloads,
            inbox_result=inbox_result,
        )

    def process_and_prepare_impact_chain(
        self,
        provider_id: str,
        items: Sequence[Any],
        *,
        as_of: datetime,
        db: Session | None = None,
        cursor: Any = None,
        max_items: int = 50,
    ) -> dict[str, Any]:
        """Operational pipeline connecting discovery -> watch -> impact chain preparation.
        
        Guarantees item-level failure isolation: If one item fails parsing or analysis,
        other valid items continue through Event Inbox and impact chain analysis.
        """
        watch_result = self.process(
            provider_id,
            items,
            as_of=as_of,
            cursor=cursor,
            max_items=max_items,
        )

        impact_candidates: list[dict[str, Any]] = []
        if db is not None:
            from app.services.event_impact_chain_service import EventImpactChainService
            for payload in watch_result.router_payloads:
                event_cid = payload.get("event_candidate_id")
                src_docs = payload.get("source_documents", [])
                source_id = src_docs[0]["source_id"] if src_docs else None
                event_type = payload.get("event_type")

                event_ref = f"dart:{source_id}" if provider_id.upper() == "DART" and source_id else str(event_cid)

                try:
                    chain_res = EventImpactChainService.get_impact_chain(
                        db,
                        event_ref,
                        score_horizon="20d",
                        score_version="v1.0",
                    )
                    impact_candidates.append({
                        "event_candidate_id": event_cid,
                        "source_id": source_id,
                        "event_type": event_type,
                        "priority_score": payload.get("priority_score"),
                        "priority_reasons": payload.get("priority_reasons"),
                        "status": payload.get("status"),
                        "impact_chain": chain_res,
                    })
                except Exception as exc:
                    impact_candidates.append({
                        "event_candidate_id": event_cid,
                        "source_id": source_id,
                        "event_type": event_type,
                        "status": "impact_analysis_failed",
                        "error": str(exc),
                    })

        return {
            "watch_result": watch_result,
            "impact_candidates": impact_candidates,
            "operational_status": "ready_for_review",
        }

    def route_candidates(
        self,
        router_payloads: Sequence[Mapping[str, Any]],
        *,
        as_of: datetime,
        db: Session | None = None,
        local_query_service: Any = None,
        score_horizon: str = "20d",
        score_version: str = "v1.0",
    ) -> list[dict[str, Any]]:
        """Helper to invoke EventRouterService on ready payloads with local provider."""
        results = []
        for payload in router_payloads:
            routed = self.router_service.route(
                db,
                payload,
                as_of=as_of,
                local_query_service=local_query_service,
                score_horizon=score_horizon,
                score_version=score_version,
            )
            results.append(routed)
        return results

    def _normalize_item(
        self,
        provider: str,
        raw: Any,
        as_of: datetime,
    ) -> tuple[EventInboxItem, bool, Any]:
        """Normalize raw input into an EventInboxItem with correction and cursor metadata."""
        if isinstance(raw, EventInboxItem):
            is_corr = (
                raw.event_state in ("correction", "revised")
                or raw.document_version not in (None, "1")
                or self._is_dart_correction(raw.title)
            )
            cursor_val = raw.published_at.isoformat() if raw.published_at else raw.source_id
            return raw, is_corr, cursor_val

        if not isinstance(raw, Mapping):
            raw_dict = {
                attr: getattr(raw, attr)
                for attr in dir(raw)
                if not attr.startswith("_") and not callable(getattr(raw, attr))
            }
        else:
            raw_dict = dict(raw)

        source_type = str(raw_dict.get("source_type") or provider).strip().upper()
        if source_type == "FRED":
            source_type = "MACRO"

        title = str(raw_dict.get("title") or raw_dict.get("report_nm") or "").strip()
        if not title:
            raise ValueError("title or report_nm is required")

        # Source ID
        source_id = str(
            raw_dict.get("source_id")
            or raw_dict.get("external_id")
            or raw_dict.get("rcept_no")
            or ""
        ).strip()
        if not source_id:
            if source_type == "MACRO" and raw_dict.get("series_id"):
                series_id = raw_dict["series_id"]
                obs_date = raw_dict.get("observation_date") or raw_dict.get("date")
                vint = raw_dict.get("realtime_start") or "latest"
                source_id = f"FRED:{series_id}:{obs_date}:{vint}"
            elif raw_dict.get("url"):
                source_id = hashlib.sha256(raw_dict["url"].encode()).hexdigest()[:32]
            else:
                raise ValueError("source_id or identifying fields required")

        # URL
        source_url = raw_dict.get("source_url") or raw_dict.get("url")
        if not source_url:
            if source_type == "DART" and raw_dict.get("rcept_no"):
                source_url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={raw_dict['rcept_no']}"
            elif source_type == "MACRO" and raw_dict.get("series_id"):
                source_url = f"https://fred.stlouisfed.org/series/{raw_dict['series_id']}"
            elif source_type == "SEC" and raw_dict.get("cik") and raw_dict.get("accession_number"):
                cik = raw_dict["cik"]
                acc = str(raw_dict["accession_number"]).replace("-", "")
                fname = raw_dict.get("filename", "doc.htm")
                source_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{fname}"
            else:
                source_url = f"https://alphachain.local/{source_type.lower()}/{source_id}"

        # Publisher
        publisher = (
            raw_dict.get("publisher")
            or raw_dict.get("flr_nm")
            or raw_dict.get("domain")
            or (
                "U.S. Securities and Exchange Commission"
                if source_type == "SEC"
                else "Financial Supervisory Service DART"
                if source_type == "DART"
                else "Federal Reserve Bank of St. Louis"
                if source_type == "MACRO"
                else "News Provider"
            )
        ).strip()

        # Timestamps
        collected_at = parse_aware_datetime(raw_dict.get("collected_at") or as_of)
        pub_raw = raw_dict.get("published_at") or raw_dict.get("rcept_dt") or raw_dict.get("filed_at")
        published_at = (
            parse_aware_datetime(pub_raw)
            if pub_raw
            else None
        )
        occurred_at = (
            parse_aware_datetime(raw_dict["occurred_at"])
            if raw_dict.get("occurred_at")
            else None
        )

        # Source Hash
        raw_hash = raw_dict.get("source_hash") or raw_dict.get("raw_hash")
        if raw_hash and re.fullmatch(r"[0-9a-fA-F]{64}", raw_hash):
            source_hash = raw_hash.lower()
        else:
            payload_str = json.dumps(
                [title, source_url, raw_dict.get("summary") or ""],
                sort_keys=True,
                ensure_ascii=False,
            )
            source_hash = hashlib.sha256(payload_str.encode("utf-8")).hexdigest()

        # Identifiers
        company_identifiers = self._build_identifiers(source_type, raw_dict)
        industries = tuple(sorted(set(raw_dict.get("industries") or ())))

        # Source-specific correction, version, state, subject, summary, and cursor
        is_correction = False
        doc_version = str(raw_dict.get("document_version") or "1")
        event_state = raw_dict.get("event_state")
        event_subject = raw_dict.get("event_subject")
        summary = raw_dict.get("summary")
        cursor_val = None

        if source_type == "DART":
            rcept_no = str(raw_dict.get("rcept_no") or source_id)
            cursor_val = rcept_no
            dart_kind = classify_dart_event(title)
            if dart_kind == "SUPPLY_CONTRACT":
                summary = (f"{summary or ''} supply contract announcement").strip()
            elif dart_kind == "FACILITY_INVESTMENT":
                summary = (f"{summary or ''} facility investment capex expansion announcement").strip()

            if self._is_dart_correction(title) or raw_dict.get("is_correction"):
                is_correction = True
                doc_version = str(raw_dict.get("document_version") or "2")
                event_state = "correction"
                base_nm = _base_report_name(title)
                corp_id = raw_dict.get("corp_code") or "corp"
                if not event_subject:
                    event_subject = f"dart:{corp_id}:{base_nm}"
            else:
                event_state = event_state or "confirmed"
                if not event_subject:
                    corp_id = raw_dict.get("corp_code") or "corp"
                    event_subject = f"dart:{corp_id}:{title}"

        elif source_type == "MACRO":
            cursor_val = raw_dict.get("observation_date") or raw_dict.get("date")
            summary = (f"{summary or ''} interest rate economic update").strip()
            if raw_dict.get("is_revision") or raw_dict.get("realtime_start") in (
                "revised",
                "revision",
            ):
                is_correction = True
                doc_version = str(raw_dict.get("document_version") or "revision")
                event_state = "revised"
            else:
                event_state = event_state or "confirmed"
            if not event_subject and raw_dict.get("series_id"):
                event_subject = f"macro:{raw_dict['series_id']}:{cursor_val}"

        elif source_type == "NEWS":
            cursor_val = published_at.isoformat() if published_at else source_id
            if raw_dict.get("is_correction") or doc_version != "1":
                is_correction = True
                event_state = event_state or "correction"

        elif source_type == "SEC":
            cursor_val = raw_dict.get("accession_number") or (
                published_at.isoformat() if published_at else source_id
            )
            if raw_dict.get("is_correction") or doc_version != "1":
                is_correction = True
                event_state = event_state or "correction"
            else:
                event_state = event_state or "planned"

        # Explicit classification only if provided and recognized; otherwise let inbox rules handle it
        existing_classification = raw_dict.get("existing_classification")
        if existing_classification in ("OTHER", "UNCLASSIFIED"):
            existing_classification = None

        inbox_item = EventInboxItem(
            source_type=source_type,
            source_id=source_id,
            source_url=source_url,
            title=title,
            publisher=publisher,
            collected_at=collected_at,
            summary=summary,
            body=raw_dict.get("body"),
            published_at=published_at,
            occurred_at=occurred_at,
            company_identifiers=company_identifiers,
            industries=industries,
            source_hash=source_hash,
            document_version=doc_version,
            existing_classification=existing_classification,
            event_subject=event_subject,
            event_state=event_state,
            amount_confirmed=bool(raw_dict.get("amount_confirmed", False)),
        )

        return inbox_item, is_correction, cursor_val

    @staticmethod
    def _is_dart_correction(title: str) -> bool:
        return bool(re.match(r"^\s*(?:\[(?:기재|첨부)?정정\]|정정신고)", title))

    @staticmethod
    def _build_identifiers(source_type: str, raw: Mapping[str, Any]) -> tuple[CompanyIdentifier, ...]:
        existing = raw.get("company_identifiers")
        if existing:
            identifiers = []
            for item in existing:
                if isinstance(item, CompanyIdentifier):
                    identifiers.append(item)
                elif isinstance(item, Mapping):
                    identifiers.append(
                        CompanyIdentifier(
                            source=item.get("source", ""),
                            value=item.get("value", ""),
                            legal_name=item.get("legal_name"),
                            confidence=item.get("confidence", "confirmed"),
                        )
                    )
            return tuple(identifiers)

        if source_type == "DART" and raw.get("corp_code"):
            return (
                CompanyIdentifier(
                    source="DART_CORP_CODE",
                    value=str(raw["corp_code"]),
                    legal_name=raw.get("corp_name"),
                    confidence="confirmed",
                ),
            )
        if source_type == "SEC" and raw.get("cik"):
            return (
                CompanyIdentifier(
                    source="CIK",
                    value=str(raw["cik"]),
                    legal_name=raw.get("company_name") or raw.get("legal_name"),
                    confidence="confirmed",
                ),
            )
        return ()

    @staticmethod
    def _is_before_or_at_cursor(item_cursor: Any, cursor: Any) -> bool:
        """Compare item cursor with reference cursor to prevent reprocessing."""
        try:
            return bool(item_cursor <= cursor)
        except TypeError:
            return False

    @staticmethod
    def _calculate_next_cursor(provider: str, current_cursor: Any, observed_cursors: list[Any]) -> Any:
        """Calculate next cursor token deterministically.

        If no reliable cursor can be found, returns the current cursor or None.
        Never generates an arbitrary, untrusted cursor.
        """
        if not observed_cursors:
            return current_cursor

        if provider == "DART":
            valid_rcepts = [c for c in observed_cursors if isinstance(c, str) and c.isdigit()]
            return max(valid_rcepts) if valid_rcepts else current_cursor

        if provider in ("NEWS", "MACRO"):
            try:
                return max(observed_cursors)
            except TypeError:
                return current_cursor

        if provider == "SEC":
            return current_cursor

        return current_cursor
