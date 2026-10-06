from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.adapters.fred_adapter import FredAdapter, FredApiError
from app.config import settings
from app.db.session import get_db
from app.models.schema import MacroObservation, MacroSeries
from app.security import require_admin
from app.services.fred_service import FredCollectionService

router = APIRouter(prefix="/macro", tags=["Macro"])


@router.post("/sync", dependencies=[Depends(require_admin)])
def sync_macro(db: Session = Depends(get_db)):
    if not settings.FRED_API_KEY:
        raise HTTPException(status_code=503, detail="FRED_API_KEY is not configured")
    try:
        service = FredCollectionService(FredAdapter(settings.FRED_API_KEY))
        return service.sync(db)
    except FredApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/series")
def list_series(db: Session = Depends(get_db)):
    rows = db.query(MacroSeries).order_by(MacroSeries.series_id).all()
    return {"count": len(rows), "data": [{
        "series_id": row.series_id, "title": row.title,
        "frequency": row.frequency, "units": row.units,
        "seasonal_adjustment": row.seasonal_adjustment,
        "last_updated": row.last_updated.isoformat() if row.last_updated else None,
    } for row in rows]}


@router.get("/regime")
def regime(as_of: date = date.today(), db: Session = Depends(get_db)):
    return {"as_of": as_of.isoformat(), "values": FredCollectionService.regime_dataset(db, as_of)}


@router.get("/{series_id}/observations")
def observations(series_id: str, limit: int = 500, db: Session = Depends(get_db)):
    rows = db.query(MacroObservation).filter(
        MacroObservation.series_id == series_id
    ).order_by(MacroObservation.observation_date.desc(), MacroObservation.vintage_date.desc()).limit(min(limit, 5000)).all()
    return {"series_id": series_id, "count": len(rows), "data": [{
        "observation_date": row.observation_date.isoformat(),
        "vintage_date": row.vintage_date.isoformat(),
        "available_at": row.available_at.isoformat(),
        "collected_at": row.collected_at.isoformat(),
        "value": float(row.value) if row.value is not None else None,
        "is_initial_release": row.is_initial_release,
    } for row in rows]}
