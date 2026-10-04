from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.adapters.us_market_adapter import YahooUSMarketAdapter
from app.db.session import get_db
from app.models.schema import (
    BacktestRun,
    Company,
    EventImpactCandidate,
    GlobalEvent,
    Security,
)
from app.security import require_admin
from app.services.event_impact_service import (
    EventImpactService,
    EventImpactV2Service,
    EventImpactV3Service,
    EventImpactV4Service,
)
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

@router.post("/{event_id}/impact-candidates/generate", dependencies=[Depends(require_admin)])
def generate_impact_candidates(event_id: int, limit: int = Query(100, ge=1, le=500),
                               db: Session = Depends(get_db)):
    return EventImpactService().generate(db, event_id, limit)


@router.post("/{event_id}/impact-candidates/generate-v2", dependencies=[Depends(require_admin)])
def generate_impact_candidates_v2(event_id: int, limit: int = Query(100, ge=1, le=500),
                                  db: Session = Depends(get_db)):
    return EventImpactV2Service().generate(db, event_id, limit)


@router.post("/{event_id}/impact-candidates/generate-v3", dependencies=[Depends(require_admin)])
def generate_impact_candidates_v3(event_id: int, limit: int = Query(100, ge=1, le=500),
                                  db: Session = Depends(get_db)):
    return EventImpactV3Service().generate(db, event_id, limit)

@router.post("/{event_id}/impact-candidates/generate-v4", dependencies=[Depends(require_admin)])
def generate_impact_candidates_v4(event_id: int, horizon: int = Query(1),
                                  limit: int = Query(100, ge=1, le=500),
                                  db: Session = Depends(get_db)):
    return EventImpactV4Service(horizon).generate(db, event_id, limit)

@router.get("/{event_id}/recommendations")
def event_recommendations(event_id: int, limit: int = Query(20, ge=1, le=100),
                          db: Session = Depends(get_db)):
    latest_run = db.query(BacktestRun).filter(
        BacktestRun.score_version == "impact-v4",
    ).order_by(BacktestRun.completed_at.desc()).first()
    report = latest_run.report if latest_run and latest_run.report else {}
    metadata = report.get("model_metadata") or {}
    holdout = (report.get("temporal_validation") or {}).get("holdout") or {}
    acceptance = (holdout.get("horizon_acceptance") or {}).get("5d") or {}
    approval_checks = {
        "calibration_current": (
            metadata.get("calibration_version") == EventImpactV4Service.calibration_version
        ),
        "minimum_holdout_events": holdout.get("events", 0) >= 10,
        "holdout_5d_passed": acceptance.get("status") == "PASSED",
    }
    approved = all(approval_checks.values())
    rows = db.query(EventImpactCandidate, Security, Company).join(
        Security, Security.id == EventImpactCandidate.security_id
    ).join(Company, Company.id == Security.company_id).filter(
        EventImpactCandidate.event_id == event_id,
        EventImpactCandidate.version == "impact-v4-5d",
    ).order_by(EventImpactCandidate.rank).limit(limit).all()
    return {
        "event_id": event_id, "model_version": "impact-v4-5d",
        "model_approved": approved,
        "approval_policy_version": "impact-v4-holdout-gate-1",
        "approval_source": "temporal_holdout_5d",
        "approval_checks": approval_checks,
        "acceptance_run_id": latest_run.id if latest_run else None,
        "acceptance": acceptance, "count": len(rows),
        "data": [{
            "rank": candidate.rank, "security_id": security.id,
            "ticker": security.ticker, "company_name": company.name,
            "market": security.market, "industry_id": candidate.industry_id,
            "impact_score": candidate.impact_score,
            "confidence": candidate.confidence,
            "explanation": candidate.explanation,
        } for candidate, security, company in rows],
    }

@router.get("/{event_id}/impact-candidates")
def list_impact_candidates(event_id: int, version: str = "impact-v4-5d",
                           limit: int = Query(100, ge=1, le=500),
                           db: Session = Depends(get_db)):
    rows = db.query(EventImpactCandidate, Security, Company).join(
        Security, Security.id == EventImpactCandidate.security_id
    ).join(Company, Company.id == Security.company_id).filter(
        EventImpactCandidate.event_id == event_id,
        EventImpactCandidate.version == version,
    ).order_by(EventImpactCandidate.rank).limit(limit).all()
    return {"event_id": event_id, "count": len(rows), "data": [{
        "rank": candidate.rank, "security_id": security.id, "ticker": security.ticker,
        "market": security.market, "company_id": company.id, "company_name": company.name,
        "industry_id": candidate.industry_id, "impact_score": candidate.impact_score,
        "confidence": candidate.confidence, "exposure": candidate.exposure,
        "version": candidate.version, "explanation": candidate.explanation,
    } for candidate, security, company in rows]}
