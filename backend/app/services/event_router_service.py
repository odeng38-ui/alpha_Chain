"""Read-only routing from Event Inbox payloads to existing Event-First analysis."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy.orm import Session

from app.services.event_first_query_service import (
    EventFirstNotFound,
    EventFirstQueryError,
    EventFirstQueryService,
)


class EventRouterError(ValueError):
    pass


class EventRouterService:
    """Validate an Inbox candidate and delegate analysis without persisting it."""

    def __init__(self, stored_query_service: EventFirstQueryService | None = None) -> None:
        self.stored_query_service = stored_query_service or EventFirstQueryService()

    def route(
        self,
        db: Session | None,
        candidate: Mapping[str, Any],
        *,
        as_of: datetime,
        stored_event_id: int | str | None = None,
        local_query_service: EventFirstQueryService | None = None,
        score_horizon: str = "20d",
        score_version: str = "v1.0",
    ) -> dict[str, Any]:
        cutoff = self._utc(as_of)
        event_id = self._required_text(candidate, "event_candidate_id")
        event_type = self._required_text(candidate, "event_type")
        status = self._required_text(candidate, "status")
        score = candidate.get("priority_score")
        reasons = tuple(candidate.get("priority_reasons") or ())
        if not isinstance(score, int) or isinstance(score, bool) or not 0 <= score <= 100:
            raise EventRouterError("priority_score must be an integer from 0 to 100")
        if not all(isinstance(reason, str) and reason for reason in reasons):
            raise EventRouterError("priority_reasons must contain non-empty strings")

        base = {
            "event_candidate_id": event_id,
            "event_type": event_type,
            "event_priority": {"score": score, "reasons": list(reasons)},
            "inbox_status": status,
            "processing_status": "pending_data",
            "anchor_company": candidate.get("anchor_company"),
            "candidates": [],
            "analysis_context": {"as_of": cutoff.isoformat(), "mode": "not_available"},
            "uncertainties": [],
            "exclusion_reasons": [],
            "event_first_connection": {
                "compatible": False,
                "path": "not_connected",
                "event_id": None,
            },
        }
        if status == "duplicate":
            return self._stop(base, "duplicate_reuse_required", "duplicate_candidate")
        if status == "needs_review":
            return self._stop(base, "needs_review", "candidate_requires_review")
        if status == "ignored":
            return self._stop(base, "ignored", "candidate_ignored_by_inbox")
        if status != "ready_for_analysis":
            return self._stop(base, "pending_data", "candidate_not_ready_for_analysis")
        if not self._confirmed_anchor(candidate.get("anchor_company")):
            return self._stop(base, "needs_review", "confirmed_anchor_required")
        if not candidate.get("source_documents"):
            return self._stop(base, "pending_data", "source_documents_required")
        if self._has_future_information(candidate, cutoff):
            return self._stop(base, "pending_data", "candidate_not_available_as_of")
        if db is None:
            return self._stop(base, "pending_data", "read_only_session_required")

        if stored_event_id is not None:
            query_service = self.stored_query_service
            query_event_id = str(stored_event_id)
            path = "stored_event"
        elif local_query_service is not None:
            query_service = local_query_service
            query_event_id = event_id
            path = "local_unstored_candidate"
        else:
            return self._stop(base, "pending_data", "unstored_candidate_provider_required")

        try:
            analysis = query_service.analyze(
                db,
                event_id=query_event_id,
                as_of=cutoff,
                score_horizon=score_horizon,
                score_version=score_version,
            )
        except EventFirstNotFound:
            return self._stop(base, "not_available", "event_analysis_not_found")
        except EventFirstQueryError as exc:
            raise EventRouterError("event analysis query failed") from exc

        result = {
            **base,
            "processing_status": "ready_for_analysis",
            "anchor_company": analysis.get("anchor") or candidate.get("anchor_company"),
            "candidates": analysis.get("candidates") or [],
            "analysis_context": analysis.get("analysis_context")
            or {"as_of": cutoff.isoformat(), "mode": "not_available"},
            "uncertainties": list(analysis.get("uncertainties") or []),
            "exclusion_reasons": list(analysis.get("empty_reasons") or [])
            + list(analysis.get("excluded_candidates") or []),
            "event_evidence": analysis.get("event_evidence") or [],
            "event_first_connection": {
                "compatible": True,
                "path": path,
                "event_id": query_event_id,
            },
        }
        mode = result["analysis_context"].get("mode")
        if mode == "fixture_demo":
            warning = "Local fixture verification is not operational approval."
            if warning not in result["uncertainties"]:
                result["uncertainties"].append(warning)
        if not result["candidates"] and not result["exclusion_reasons"]:
            result["processing_status"] = "pending_data"
            result["exclusion_reasons"].append("no_related_candidate_available")
        return result

    @staticmethod
    def _stop(
        base: dict[str, Any], processing_status: str, reason: str
    ) -> dict[str, Any]:
        return {
            **base,
            "processing_status": processing_status,
            "exclusion_reasons": [reason],
        }

    @staticmethod
    def _required_text(candidate: Mapping[str, Any], field: str) -> str:
        value = candidate.get(field)
        if not isinstance(value, str) or not value.strip():
            raise EventRouterError(f"{field} is required")
        return value.strip()

    @staticmethod
    def _confirmed_anchor(value: object) -> bool:
        if not isinstance(value, Mapping):
            return False
        source = value.get("source")
        identifier = value.get("value")
        confidence = value.get("confidence")
        return bool(source and identifier and confidence == "confirmed")

    def _has_future_information(
        self, candidate: Mapping[str, Any], cutoff: datetime
    ) -> bool:
        available_at = self._parse_optional(candidate.get("available_at"), "available_at")
        if available_at is not None and available_at > cutoff:
            return True
        for document in candidate.get("source_documents") or ():
            if not isinstance(document, Mapping):
                raise EventRouterError("source_documents must contain objects")
            for field in ("published_at", "collected_at", "occurred_at"):
                value = self._parse_optional(document.get(field), field)
                if value is not None and value > cutoff:
                    return True
        return False

    @staticmethod
    def _parse_optional(value: object, field: str) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise EventRouterError(f"{field} must be an ISO datetime") from exc
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise EventRouterError(f"{field} must be timezone-aware")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise EventRouterError("as_of must be timezone-aware")
        return value.astimezone(timezone.utc)
