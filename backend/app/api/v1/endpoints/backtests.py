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
from app.services.news_candidate_backtest_service import NewsCandidateBacktestService

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


class EventImpactBacktestRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    start: Optional[date] = None
    end: Optional[date] = None
    max_events: int = Field(default=10, ge=1, le=50)
    candidates_per_event: int = Field(default=20, ge=1, le=100)
    horizons: list[int] = Field(default_factory=lambda: [1, 5])
    version: str = "impact-v4"
    baseline_run_id: Optional[int] = Field(default=None, ge=1)


@router.post("/event-impact", dependencies=[Depends(require_admin)])
def run_event_impact_backtest(req: EventImpactBacktestRequest,
                              db: Session = Depends(get_db)):
    if not req.horizons or any(item < 1 or item > 20 for item in req.horizons):
        raise HTTPException(status_code=422, detail="horizons must be between 1 and 20")
    if req.version not in {"impact-v2", "impact-v3", "impact-v4"}:
        raise HTTPException(status_code=422, detail="unsupported event impact version")
    if req.baseline_run_id is not None:
        baseline = db.get(BacktestRun, req.baseline_run_id)
        if baseline is None or baseline.score_version != "impact-v4":
            raise HTTPException(status_code=422, detail="invalid impact-v4 baseline run")
        if req.version != "impact-v4":
            raise HTTPException(status_code=422, detail="baseline is only supported for impact-v4")
    row = EventImpactBacktestService(req.version).run(
        db, req.name, req.start, req.end, req.max_events,
        req.candidates_per_event, tuple(sorted(set(req.horizons))),
        req.baseline_run_id,
    )
    return {"id": row.id, "status": row.status,
            "dataset_hash": row.dataset_hash, "report": row.report}


def _event_metric_snapshot(run: BacktestRun, horizon: str):
    metric = ((run.report or {}).get("metrics") or {}).get(horizon) or {}
    return {
        "observations": metric.get("observations", 0),
        "direction_hit_rate": metric.get("direction_hit_rate"),
        "average_aligned_return": metric.get("average_aligned_return"),
    }


def _metric_delta(current, baseline):
    return {
        key: (round(current[key] - baseline[key], 8)
              if current.get(key) is not None and baseline.get(key) is not None else None)
        for key in ("direction_hit_rate", "average_aligned_return")
    }


@router.get("/event-impact/calibration-status")
def event_impact_calibration_status(db: Session = Depends(get_db)):
    rows = db.query(BacktestRun).filter(
        BacktestRun.score_version == "impact-v4",
    ).order_by(BacktestRun.created_at.desc(), BacktestRun.id.desc()).all()
    current = next((row for row in rows if (
        ((row.report or {}).get("model_metadata") or {}).get("calibration_version")
    )), None)
    if current is None:
        return {"status": "NOT_RUN", "current": None, "baseline": None,
                "comparison": {}}
    metadata = (current.report or {}).get("model_metadata") or {}
    baseline_id = metadata.get("baseline_run_id")
    baseline = db.get(BacktestRun, baseline_id) if baseline_id else None
    if baseline is None or baseline.score_version != "impact-v4":
        return {
            "status": "BASELINE_REQUIRED",
            "calibration_version": metadata.get("calibration_version"),
            "current": {"run_id": current.id, "status": current.status},
            "baseline": None, "comparison": {},
        }
    comparison = {}
    for horizon in ("1d", "5d"):
        current_metric = _event_metric_snapshot(current, horizon)
        baseline_metric = _event_metric_snapshot(baseline, horizon)
        comparison[horizon] = {
            "current": current_metric,
            "baseline": baseline_metric,
            "delta": _metric_delta(current_metric, baseline_metric),
        }
    return {
        "status": "COMPARABLE",
        "calibration_version": metadata.get("calibration_version"),
        "current": {"run_id": current.id, "status": current.status},
        "baseline": {"run_id": baseline.id, "status": baseline.status},
        "comparison": comparison,
    }


class NewsCandidateBacktestRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    horizons: list[int] = Field(default_factory=lambda: [1, 5, 20])
    candidate_limit: int = Field(default=500, ge=1, le=2000)


@router.post("/news-candidates", dependencies=[Depends(require_admin)])
def run_news_candidate_backtest(req: NewsCandidateBacktestRequest,
                                db: Session = Depends(get_db)):
    if not req.horizons or any(item not in {1, 5, 20} for item in req.horizons):
        raise HTTPException(status_code=422, detail="horizons must be selected from 1, 5, 20")
    try:
        row = NewsCandidateBacktestService().run(
            db, req.name, tuple(sorted(set(req.horizons))), req.candidate_limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"id": row.id, "status": row.status,
            "dataset_hash": row.dataset_hash, "report": row.report}

@router.get("/{run_id}")
def get_backtest(run_id: int, db: Session = Depends(get_db)):
    row = db.get(BacktestRun, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="backtest not found")
    return {"id": row.id, "name": row.name, "status": row.status,
            "score_version": row.score_version, "horizon": row.horizon,
            "dataset_hash": row.dataset_hash, "config": row.config,
            "parameter_adjustments": row.parameter_adjustments, "report": row.report}
