from datetime import date
from typing import Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.schema import AlphaWeightConfig, FeatureSnapshot, ScoreSnapshot
from app.security import require_admin
from app.services.alpha_score_service import AlphaScoreService
from app.services.score_batch_verification import verify_score_batch

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


@router.get("/batch/verification", dependencies=[Depends(require_admin)])
def verify_batch(
    as_of_date: date,
    horizon: str = Query(default="20d", min_length=1, max_length=20),
    version: str = Query(default="v1.0", min_length=1, max_length=20),
    target_policy: Literal["active_common", "all_common"] = "active_common",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    result = verify_score_batch(
        db,
        as_of_date=as_of_date,
        horizon=horizon,
        version=version,
        target_policy=target_policy,
        missing_offset=(page - 1) * page_size,
        missing_limit=page_size,
    ).to_dict()
    result["missing_page"] = page
    result["missing_page_size"] = page_size
    return result


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
