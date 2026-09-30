from datetime import date
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.schema import AlphaWeightConfig, FeatureSnapshot, ScoreSnapshot
from app.security import require_admin
from app.services.alpha_score_service import AlphaScoreService

router = APIRouter(prefix="/scores", tags=["Alpha Score"])
service = AlphaScoreService()


class ScoreBatchRequest(BaseModel):
    as_of_date: date
    security_ids: Optional[List[int]] = None
    horizon: str = "20d"
    version: str = "v1.0"


class WeightConfigRequest(BaseModel):
    version: str = Field(min_length=1, max_length=20)
    weights: Dict[str, float]
    changed_by: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=500)


def serialize_score(row: ScoreSnapshot, features):
    return {
        "security_id": row.security_id, "as_of_date": row.as_of_date,
        "horizon": row.horizon, "version": row.version,
        "score": row.total, "confidence": row.confidence,
        "completeness": row.completeness, "components": row.component,
        "weights": row.weights, "explanations": row.explanations,
        "config_hash": row.config_hash,
        "features": [{
            "name": item.feature_name,
            "value": float(item.value) if item.value is not None else None,
            "formula": item.formula, "inputs": item.inputs,
            "source_available_at": item.source_available_at,
            "missing_reason": item.missing_reason,
            "version": item.feature_version,
        } for item in features],
    }


@router.post("/batch", dependencies=[Depends(require_admin)])
def calculate_batch(req: ScoreBatchRequest, db: Session = Depends(get_db)):
    return service.batch(db, req.as_of_date, req.security_ids, req.horizon, req.version)


@router.post("/security/{security_id}/calculate", dependencies=[Depends(require_admin)])
def calculate_score(
    security_id: int, as_of: date, horizon: str = "20d", version: str = "v1.0",
    db: Session = Depends(get_db),
):
    try:
        row = service.score(db, security_id, as_of, horizon, version)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    features = db.query(FeatureSnapshot).filter_by(
        security_id=security_id, as_of_date=as_of, feature_version=version,
    ).order_by(FeatureSnapshot.feature_name).all()
    return serialize_score(row, features)


@router.get("/security/{security_id}")
def score_explanation(
    security_id: int, as_of: date, horizon: str = "20d", version: str = "v1.0",
    db: Session = Depends(get_db),
):
    row = db.query(ScoreSnapshot).filter_by(
        security_id=security_id, as_of_date=as_of, horizon=horizon, version=version,
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="score snapshot not found")
    features = db.query(FeatureSnapshot).filter_by(
        security_id=security_id, as_of_date=as_of, feature_version=version,
    ).order_by(FeatureSnapshot.feature_name).all()
    return serialize_score(row, features)


@router.post("/config/weights", dependencies=[Depends(require_admin)])
def create_weight_version(req: WeightConfigRequest, db: Session = Depends(get_db)):
    try:
        row = service.save_weights(db, req.version, req.weights, req.changed_by, req.reason)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"version": row.version, "weights": row.weights, "changed_by": row.changed_by,
            "reason": row.reason, "created_at": row.created_at}


@router.get("/config/history")
def weight_history(db: Session = Depends(get_db)):
    rows = db.query(AlphaWeightConfig).order_by(AlphaWeightConfig.created_at.desc()).all()
    return [{"version": row.version, "weights": row.weights, "changed_by": row.changed_by,
             "reason": row.reason, "created_at": row.created_at} for row in rows]
