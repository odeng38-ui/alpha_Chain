from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.adapters.us_market_adapter import YahooUSMarketAdapter
from app.db.session import get_db
from app.models.schema import GlobalEvent
from app.security import require_admin
from app.services.us_market_event_service import USMarketEventService

router = APIRouter(prefix="/global-events", tags=["Global Events"])


@router.post("/us-market/sync", dependencies=[Depends(require_admin)])
def sync_us_market(as_of: date | None = None, lookback_days: int = Query(180, ge=60, le=730),
                   db: Session = Depends(get_db)):
    return USMarketEventService(YahooUSMarketAdapter()).sync(db, as_of, lookback_days)


@router.get("")
def list_events(symbol: str | None = None, direction: str | None = None,
                limit: int = Query(100, ge=1, le=1000), db: Session = Depends(get_db)):
    query = db.query(GlobalEvent)
    if symbol:
        query = query.filter(GlobalEvent.symbol == symbol)
    if direction:
        query = query.filter(GlobalEvent.direction == direction.upper())
    rows = query.order_by(GlobalEvent.occurred_at.desc()).limit(limit).all()
    return {"count": len(rows), "data": [{
        "id": row.id, "external_id": row.external_id, "source": row.source,
        "origin_country": row.origin_country, "event_kind": row.event_kind,
        "symbol": row.symbol, "title": row.title, "summary": row.summary,
        "direction": row.direction, "occurred_at": row.occurred_at.isoformat(),
        "available_at": row.available_at.isoformat(), "return_1d": row.return_1d,
        "zscore_20d": row.zscore_20d, "shock_score": row.shock_score,
    } for row in rows]}