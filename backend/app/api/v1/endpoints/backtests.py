from datetime import date
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.schema import BacktestRun
from app.security import require_admin
from app.services.backtest_service import BacktestService
from app.services.event_impact_backtest_service import EventImpactBacktestService

router = APIRouter(prefix="/backtests", tags=["Backtests"])
service = BacktestService()


class BacktestRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    score_version: str = "v1.0"
    horizon: str = "20d"
    start: Optional[date] = None
    end: Optional[date] = None
    config_override: Optional[Dict] = None


@router.post("", dependencies=[Depends(require_admin)])
def run_backtest(req: BacktestRequest, db: Session = Depends(get_db)):
    try:
        row = service.run(
            db, req.name, req.score_version, req.horizon,
            req.start, req.end, req.config_override,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"id": row.id, "status": row.status, "dataset_hash": row.dataset_hash,
            "report": row.report}


@router.get("")
def list_backtests(limit: int = Query(20, ge=1, le=200), db: Session = Depends(get_db)):
    rows = db.query(BacktestRun).order_by(BacktestRun.created_at.desc()).limit(limit).all()
    return [{"id": row.id, "name": row.name, "status": row.status,
             "score_version": row.score_version, "horizon": row.horizon,
             "dataset_hash": row.dataset_hash, "created_at": row.created_at} for row in rows]


@router.get("/{run_id}")
def get_backtest(run_id: int, db: Session = Depends(get_db)):
    row = db.get(BacktestRun, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="backtest not found")
    return {"id": row.id, "name": row.name, "status": row.status,
            "score_version": row.score_version, "horizon": row.horizon,
            "dataset_hash": row.dataset_hash, "config": row.config,
            "parameter_adjustments": row.parameter_adjustments, "report": row.report}

class EventImpactBacktestRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    start: Optional[date] = None
    end: Optional[date] = None
    max_events: int = Field(default=10, ge=1, le=50)
    candidates_per_event: int = Field(default=20, ge=1, le=100)
    horizons: list[int] = Field(default_factory=lambda: [1, 5])


@router.post("/event-impact", dependencies=[Depends(require_admin)])
def run_event_impact_backtest(req: EventImpactBacktestRequest,
                              db: Session = Depends(get_db)):
    if not req.horizons or any(item < 1 or item > 20 for item in req.horizons):
        raise HTTPException(status_code=422, detail="horizons must be between 1 and 20")
    row = EventImpactBacktestService().run(
        db, req.name, req.start, req.end, req.max_events,
        req.candidates_per_event, tuple(sorted(set(req.horizons))),
    )
    return {"id": row.id, "status": row.status,
            "dataset_hash": row.dataset_hash, "report": row.report}
