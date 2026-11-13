"""Deterministic, database-free Event Inbox processing.

The inbox accepts already collected metadata and optional text.  It never fetches
documents, writes to a database, or treats processing readiness as verification.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Sequence
from urllib.parse import urlsplit, urlunsplit

from app.services.news_classification_service import (
    ClassificationResult,
    NewsClassificationService,
)

SUPPORTED_SOURCES = frozenset({"NEWS", "SEC", "DART", "OFFICIAL_WEB", "MACRO"})


class EventType(StrEnum):
    CAPEX_EXPANSION = "CAPEX_EXPANSION"
    SUPPLY_CONTRACT = "SUPPLY_CONTRACT"
    PRODUCTION_CHANGE = "PRODUCTION_CHANGE"
    EARNINGS_CHANGE = "EARNINGS_CHANGE"
    POLICY_REGULATION = "POLICY_REGULATION"
    MACRO_CHANGE = "MACRO_CHANGE"
    TECH_INDUSTRY = "TECH_INDUSTRY"
    OTHER = "OTHER"
    UNCLASSIFIED = "UNCLASSIFIED"


class InboxStatus(StrEnum):
    NEW = "new"
    CLASSIFIED = "classified"
    DUPLICATE = "duplicate"
    GROUPED = "grouped"
    NEEDS_REVIEW = "needs_review"
    READY_FOR_ANALYSIS = "ready_for_analysis"
    IGNORED = "ignored"


@dataclass(frozen=True)
class CompanyIdentifier:
    source: str
    value: str
    legal_name: str | None = None
    confidence: str = "confirmed"


@dataclass(frozen=True)
class EventInboxItem:
    source_type: str
    source_id: str
    source_url: str
    title: str
    publisher: str
    collected_at: datetime
    summary: str | None = None
    body: str | None = None
    published_at: datetime | None = None
    occurred_at: datetime | None = None
    company_identifiers: tuple[CompanyIdentifier, ...] = ()
    industries: tuple[str, ...] = ()
    source_hash: str | None = None
    document_version: str | None = None
    existing_classification: str | None = None
    event_subject: str | None = None
    event_state: str | None = None
    amount_confirmed: bool = False


@dataclass(frozen=True)
class SourceDocument:
    document_id: str
    source_type: str
    source_id: str
    source_url: str
    publisher: str
    title: str
    summary: str | None
    source_hash: str | None
    document_version: str | None
    published_at: datetime | None
    collected_at: datetime
    occurred_at: datetime | None
    status: InboxStatus
    duplicate_of: str | None = None


@dataclass(frozen=True)
class EventCandidate:
    event_candidate_id: str
    event_type: EventType
    priority_score: int
    priority_reasons: tuple[str, ...]
    anchor_company: CompanyIdentifier | None
    industries: tuple[str, ...]
    status: InboxStatus
    source_documents: tuple[SourceDocument, ...]
    available_at: datetime | None
    requires_deep_verification: bool
    event_state: str | None
    classification_source: str

    def to_router_payload(self) -> dict[str, object]:
        """Return the stable, JSON-compatible contract consumed by a future router."""
        payload = asdict(self)
        payload["event_type"] = self.event_type.value
        payload["status"] = self.status.value
        payload["source_documents"] = [
            {**asdict(row), "status": row.status.value} for row in self.source_documents
        ]
        payload["available_at"] = (
            self.available_at.isoformat() if self.available_at is not None else None
        )
        for row in payload["source_documents"]:
            for field in ("published_at", "collected_at", "occurred_at"):
                value = row[field]
                row[field] = value.isoformat() if value is not None else None
        return payload


@dataclass(frozen=True)
class InboxResult:
    candidates: tuple[EventCandidate, ...]
    documents: tuple[SourceDocument, ...]


_EXISTING_KIND_MAP = {
    "FED_POLICY": EventType.MACRO_CHANGE,
    "INFLATION": EventType.MACRO_CHANGE,
    "TRADE_POLICY": EventType.POLICY_REGULATION,
    "SEMICONDUCTOR": EventType.TECH_INDUSTRY,
    "AI_TECH": EventType.TECH_INDUSTRY,
    "ENERGY": EventType.TECH_INDUSTRY,
    "CORPORATE_EARNINGS": EventType.EARNINGS_CHANGE,
    "MARKET_MOVEMENT": EventType.OTHER,
    "OTHER": EventType.UNCLASSIFIED,
}

_EVENT_RULES = (
    (EventType.CAPEX_EXPANSION, ("capital expenditure", "capex", "facility investment", "new fab", "manufacturing expansion", "expand production capacity", "investment plan", "planned investment", "expanded united states investment", "expanded u.s. investment")),
    (EventType.SUPPLY_CONTRACT, ("supply agreement", "supply contract", "purchase order", "awarded a contract", "customer contract")),
    (EventType.PRODUCTION_CHANGE, ("production cut", "production increase", "capacity reduction", "utilization rate", "output reduction")),
    (EventType.EARNINGS_CHANGE, ("earnings", "revenue guidance", "profit guidance", "financial results")),
    (EventType.POLICY_REGULATION, ("regulation", "tariff", "export control", "sanction", "government policy")),
    (EventType.MACRO_CHANGE, ("interest rate", "inflation", "consumer price index", "unemployment rate", "gdp")),
    (EventType.TECH_INDUSTRY, ("technology transition", "industry standard", "semiconductor technology", "artificial intelligence")),
)


class EventInboxError(ValueError):
    pass


class EventInboxService:
    """Normalize and select event candidates without I/O or mutable global state."""

    def process(
        self,
        items: Sequence[EventInboxItem],
        *,
        as_of: datetime,
    ) -> InboxResult:
        cutoff = self._utc(as_of, "as_of")
        normalized = [self._normalize(item) for item in items]
        documents: list[SourceDocument] = []
        unique_items: list[tuple[EventInboxItem, SourceDocument]] = []
        seen: dict[str, str] = {}

        for item in normalized:
            key = self._document_key(item)
            document_id = self._stable_id("doc", key)
            unavailable = item.collected_at > cutoff or (
                item.published_at is not None and item.published_at > cutoff
            )
            if key in seen:
                document = self._document(
                    item, document_id, InboxStatus.DUPLICATE, duplicate_of=seen[key]
                )
            elif unavailable:
                seen[key] = document_id
                document = self._document(item, document_id, InboxStatus.IGNORED)
            else:
                seen[key] = document_id
                document = self._document(item, document_id, InboxStatus.NEW)
                unique_items.append((item, document))
            documents.append(document)

        groups: dict[str, list[tuple[EventInboxItem, SourceDocument, EventType, str]]] = {}
        for item, document in unique_items:
            event_type, source = self._classify(item)
            group_key = self._event_group_key(item, event_type, document.document_id)
            groups.setdefault(group_key, []).append((item, document, event_type, source))

        candidates = tuple(
            self._candidate(group_key, rows)
            for group_key, rows in sorted(groups.items())
        )
        return InboxResult(candidates=candidates, documents=tuple(documents))

    def _normalize(self, item: EventInboxItem) -> EventInboxItem:
        source_type = item.source_type.strip().upper()
        if source_type not in SUPPORTED_SOURCES:
            raise EventInboxError(f"unsupported source_type: {source_type}")
        if not item.source_id.strip() or not item.title.strip() or not item.publisher.strip():
            raise EventInboxError("source_id, title, and publisher are required")
        url = self._canonical_url(item.source_url)
        collected_at = self._utc(item.collected_at, "collected_at")
        published_at = self._optional_utc(item.published_at, "published_at")
        occurred_at = self._optional_utc(item.occurred_at, "occurred_at")
        source_hash = item.source_hash.lower() if item.source_hash else None
        if source_hash is not None and not re.fullmatch(r"[0-9a-f]{64}", source_hash):
            raise EventInboxError("source_hash must be a SHA-256 hex digest")
        identifiers = tuple(
            CompanyIdentifier(
                source=value.source.strip().upper(),
                value=value.value.strip(),
                legal_name=value.legal_name.strip() if value.legal_name else None,
                confidence=value.confidence.strip().lower(),
            )
            for value in item.company_identifiers
            if value.source.strip() and value.value.strip()
        )
        return EventInboxItem(
            **{
                **asdict(item),
                "source_type": source_type,
                "source_id": item.source_id.strip(),
                "source_url": url,
                "title": item.title.strip(),
                "publisher": item.publisher.strip(),
                "collected_at": collected_at,
                "published_at": published_at,
                "occurred_at": occurred_at,
                "company_identifiers": identifiers,
                "industries": tuple(sorted(set(item.industries))),
                "source_hash": source_hash,
                "event_subject": item.event_subject.strip().lower() if item.event_subject else None,
                "event_state": item.event_state.strip().lower() if item.event_state else None,
            }
        )

    def _classify(self, item: EventInboxItem) -> tuple[EventType, str]:
        if item.existing_classification:
            mapped = _EXISTING_KIND_MAP.get(item.existing_classification.strip().upper())
            if mapped is not None:
                return mapped, f"news_classification:{NewsClassificationService.version}"
        text = " ".join(filter(None, (item.title, item.summary, item.body))).lower()
        for event_type, terms in _EVENT_RULES:
            if any(term in text for term in terms):
                return event_type, "event_inbox_rules:v1"
        if item.source_type == "NEWS":
            existing: ClassificationResult = NewsClassificationService.classify_text(item.title)
            mapped = _EXISTING_KIND_MAP.get(existing.event_kind, EventType.UNCLASSIFIED)
            return mapped, f"news_classification:{NewsClassificationService.version}"
        return EventType.UNCLASSIFIED, "event_inbox_rules:v1"

    def _event_group_key(
        self,
        item: EventInboxItem,
        event_type: EventType,
        fallback: str,
    ) -> str:
        anchor = self._anchor(item)
        # A shared, explicit subject plus an exact identifier is required for automatic grouping.
        if item.event_subject and anchor is not None:
            return json.dumps(
                [anchor.source, anchor.value, event_type.value, item.event_subject],
                separators=(",", ":"),
            )
        return fallback

    def _candidate(
        self,
        group_key: str,
        rows: list[tuple[EventInboxItem, SourceDocument, EventType, str]],
    ) -> EventCandidate:
        first = rows[0]
        event_type = first[2]
        items = [row[0] for row in rows]
        anchor = self._anchor(first[0])
        classification_sources = sorted({row[3] for row in rows})
        industries = tuple(sorted({value for item in items for value in item.industries}))
        status = InboxStatus.READY_FOR_ANALYSIS
        if event_type is EventType.UNCLASSIFIED or anchor is None:
            status = InboxStatus.NEEDS_REVIEW
        priority, reasons = self._priority(items, event_type, anchor, len(rows) > 1)
        documents = tuple(
            SourceDocument(
                **{
                    **asdict(row[1]),
                    "status": InboxStatus.GROUPED if len(rows) > 1 else InboxStatus.CLASSIFIED,
                }
            )
            for row in rows
        )
        available_times = [
            max(filter(None, (item.published_at, item.collected_at))) for item in items
        ]
        event_states = {item.event_state for item in items if item.event_state}
        return EventCandidate(
            event_candidate_id=self._stable_id("evt", group_key),
            event_type=event_type,
            priority_score=priority,
            priority_reasons=reasons,
            anchor_company=anchor,
            industries=industries,
            status=status,
            source_documents=documents,
            available_at=max(available_times) if available_times else None,
            requires_deep_verification=True,
            event_state=next(iter(event_states)) if len(event_states) == 1 else None,
            classification_source="+".join(classification_sources),
        )

    @staticmethod
    def _priority(
        items: list[EventInboxItem],
        event_type: EventType,
        anchor: CompanyIdentifier | None,
        grouped: bool,
    ) -> tuple[int, tuple[str, ...]]:
        score = 10
        reasons = ["new_information"]
        source_score = max(
            {"SEC": 30, "DART": 30, "OFFICIAL_WEB": 25, "MACRO": 25, "NEWS": 10}[
                item.source_type
            ]
            for item in items
        )
        score += source_score
        reasons.append("official_source" if source_score >= 25 else "news_source")
        if event_type not in {EventType.UNCLASSIFIED, EventType.OTHER}:
            score += 15
            reasons.append("actionable_event_type")
        if anchor is not None and anchor.confidence == "confirmed":
            score += 15
            reasons.append("identified_anchor")
        if any(item.amount_confirmed for item in items):
            score += 10
            reasons.append("confirmed_scale")
        if any(item.event_state in {"confirmed", "completed"} for item in items):
            score += 10
            reasons.append("confirmed_event")
        elif any(item.event_state in {"planned", "conditional"} for item in items):
            reasons.append("planned_or_conditional")
        if grouped:
            reasons.append("corroborating_documents")
        return min(score, 100), tuple(reasons)

    @staticmethod
    def _anchor(item: EventInboxItem) -> CompanyIdentifier | None:
        confirmed = [row for row in item.company_identifiers if row.confidence == "confirmed"]
        return confirmed[0] if len(confirmed) == 1 else None

    @staticmethod
    def _document_key(item: EventInboxItem) -> str:
        # A changed hash/version is a correction, not a duplicate.
        identity = [item.source_type, item.source_id, item.document_version, item.source_hash]
        if item.source_hash is None and item.document_version is None:
            identity.append(item.source_url)
        return json.dumps(identity, separators=(",", ":"))

    @staticmethod
    def _document(
        item: EventInboxItem,
        document_id: str,
        status: InboxStatus,
        duplicate_of: str | None = None,
    ) -> SourceDocument:
        return SourceDocument(
            document_id=document_id,
            source_type=item.source_type,
            source_id=item.source_id,
            source_url=item.source_url,
            publisher=item.publisher,
            title=item.title,
            summary=item.summary,
            source_hash=item.source_hash,
            document_version=item.document_version,
            published_at=item.published_at,
            collected_at=item.collected_at,
            occurred_at=item.occurred_at,
            status=status,
            duplicate_of=duplicate_of,
        )

    @staticmethod
    def _canonical_url(value: str) -> str:
        parsed = urlsplit(value.strip())
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            raise EventInboxError("source_url must be an absolute HTTP(S) URL")
        host = parsed.hostname.lower()
        port = f":{parsed.port}" if parsed.port else ""
        return urlunsplit((parsed.scheme.lower(), host + port, parsed.path, parsed.query, ""))

    @staticmethod
    def _utc(value: datetime, field: str) -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise EventInboxError(f"{field} must be timezone-aware")
        return value.astimezone(timezone.utc)

    def _optional_utc(self, value: datetime | None, field: str) -> datetime | None:
        return self._utc(value, field) if value is not None else None

    @staticmethod
    def _stable_id(prefix: str, value: str) -> str:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
        return f"{prefix}_{digest}"

