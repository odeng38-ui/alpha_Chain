"""Selected phase-d2 GET endpoints, backed exclusively by existing stored data."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.schema import GlobalEvent
from app.services.event_first_query_service import (
    EventFirstNotFound,
    EventFirstQueryError,
    EventFirstQueryService,
)
from app.services.event_first_stored_data_bridge import EventFirstStoredDataBridge

router = APIRouter(prefix="/event-first", tags=["Event-First"])


def query_service() -> EventFirstQueryService:
    return EventFirstStoredDataBridge()


@router.get("/events")
def list_event_first_events(
    limit: int = Query(100, ge=1, le=1000),
    include_demo: bool = False,
    db: Session = Depends(get_db),
):
    if include_demo:
        raise HTTPException(403, "fixture demos are not enabled in this integration")
    if db.new or db.dirty or db.deleted:
        raise HTTPException(422, "clean read-only session required")
    try:
        with db.no_autoflush:
            rows = db.query(GlobalEvent).order_by(GlobalEvent.occurred_at.desc()).limit(limit).all()
            events = [{
                "id": row.id, "external_id": row.external_id, "source": row.source,
                "source_type": "stored", "is_demo": False,
                "origin_country": row.origin_country, "event_kind": row.event_kind,
                "symbol": row.symbol, "title": row.title, "summary": row.summary,
                "direction": row.direction,
                "occurred_at": row.occurred_at.isoformat() if row.occurred_at else None,
                "available_at": row.available_at.isoformat() if row.available_at else None,
                "return_1d": row.return_1d, "zscore_20d": row.zscore_20d,
                "shock_score": row.shock_score,
            } for row in rows]
        return {"count": len(events), "data": events}
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(503, "event_first_storage_unavailable") from exc


@router.get("/events/{event_id}/analysis")
def event_analysis(
    event_id: str,
    as_of: datetime = Query(...),
    score_horizon: str = Query("20d", min_length=1, max_length=20),
    score_version: str = Query("v1.0", min_length=1, max_length=20),
    db: Session = Depends(get_db),
    service: EventFirstQueryService = Depends(query_service),
):
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise HTTPException(422, "as_of must be timezone-aware")
    try:
        analysis = service.analyze(db, event_id=event_id, as_of=as_of,
                                   score_horizon=score_horizon, score_version=score_version)
    except EventFirstNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except EventFirstQueryError as exc:
        raise HTTPException(422, str(exc)) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(503, "event_first_storage_unavailable") from exc
    reasons = list(analysis.get("empty_reasons") or [])
    return {
        **analysis,
        "processing_status": "ready_for_analysis" if analysis.get("candidates") else "pending_data",
        "event_candidate_id": str(analysis["event"]["event_id"]),
        "is_demo": False, "source_type": "stored",
        "anchor_company": analysis.get("anchor"),
        "exclusion_reasons": reasons,
        "event_first_connection": {
            "compatible": True, "path": "stored_event",
            "event_id": str(analysis["event"]["event_id"]),
        },
    }
