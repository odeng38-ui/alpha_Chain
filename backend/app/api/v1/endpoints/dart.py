from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.adapters.dart_adapter import DartAdapter, DartApiError
from app.config import settings
from app.db.session import get_db
from app.jobs import dart_collection_runner
from app.models.schema import Company, DartSyncState, Filing
from app.security import require_admin
from app.services.dart_service import DartCollectionService
from app.services.industry_service import sync_industry_batch

router = APIRouter(prefix="/dart", tags=["DART"])


class DartSyncRequest(BaseModel):
    company_ids: Optional[list[int]] = None
    start_date: date = Field(default_factory=lambda: date.today() - timedelta(days=30))
    end_date: date = Field(default_factory=date.today)
    limit: int = Field(default=100, ge=1, le=500)
    download_documents: bool = False



class DartBackgroundRequest(BaseModel):
    start_date: date = Field(default_factory=lambda: date.today() - timedelta(days=365))
    end_date: date = Field(default_factory=date.today)
    batch_size: int = Field(default=2, ge=1, le=20)
    financial_years: list[str] = Field(default_factory=lambda: [str(date.today().year - 2), str(date.today().year - 1)])
    retry_failed: bool = False
@router.post("/sync", dependencies=[Depends(require_admin)])
def sync_dart(req: DartSyncRequest, db: Session = Depends(get_db)):
    query = db.query(Company).filter(Company.corp_code.isnot(None))
    if req.company_ids:
        query = query.filter(Company.id.in_(req.company_ids))
    companies = query.order_by(Company.id).limit(req.limit).all()
    service = DartCollectionService(DartAdapter(settings.DART_API_KEY), settings.DART_RAW_DIR)
    results = []
    for company in companies:
        state = db.get(DartSyncState, company.id)
        start = max(req.start_date, state.last_filing_date + timedelta(days=1)) if state and state.last_filing_date else req.start_date
        if start > req.end_date:
            results.append({"company_id": company.id, "status": "up_to_date"})
            continue
        try:
            result = service.sync_company(db, company, start, req.end_date, req.download_documents)
            results.append({"company_id": company.id, "status": "success", **result})
        except (DartApiError, ValueError) as exc:
            db.rollback()
            state = db.get(DartSyncState, company.id) or DartSyncState(company_id=company.id)
            db.add(state)
            state.status = "FAILED"
            state.last_error = str(exc)
            db.commit()
            results.append({"company_id": company.id, "status": "failed", "error": str(exc)})
    return {"companies": len(companies), "results": results}


@router.post("/collection-run/start", dependencies=[Depends(require_admin)])
def start_dart_collection(req: DartBackgroundRequest):
    if req.start_date > req.end_date:
        raise HTTPException(status_code=400, detail="start_date must not be after end_date")
    years = sorted({year for year in req.financial_years if len(year) == 4 and year.isdigit()})
    if not years:
        raise HTTPException(status_code=400, detail="at least one valid financial year is required")
    return dart_collection_runner.start_collection(
        req.batch_size, req.start_date, req.end_date, years, req.retry_failed,
    )


@router.post("/collection-run/stop", dependencies=[Depends(require_admin)])
def stop_dart_collection():
    return dart_collection_runner.stop_collection()


@router.get("/collection-run/status")
def dart_collection_status():
    return dart_collection_runner.runner_status()


@router.get("/industries/summary")
def industry_summary(db: Session = Depends(get_db)):
    rows = db.query(Company.industry_id, func.count(Company.id)).group_by(Company.industry_id).order_by(func.count(Company.id).desc()).all()
    return {
        "classified": sum(count for industry, count in rows if industry),
        "unclassified": sum(count for industry, count in rows if not industry),
        "groups": [{"industry_id": industry or "UNCLASSIFIED", "count": count} for industry, count in rows],
    }

@router.get("/filings")
def list_stored_filings(company_id: Optional[int] = None, limit: int = 100,
                        db: Session = Depends(get_db)):
    query = db.query(Filing)
    if company_id:
        query = query.filter(Filing.company_id == company_id)
    rows = query.order_by(Filing.available_at.desc()).limit(min(limit, 500)).all()
    return {"count": len(rows), "data": [{
        "rcept_no": row.rcept_no,
        "company_id": row.company_id,
        "report_name": row.report_name,
        "available_at": row.available_at.isoformat(),
        "correction_of": row.correction_of,
        "raw_ref": row.raw_ref,
        "parser_version": row.parser_version,
    } for row in rows]}

@router.post("/industries/sync", dependencies=[Depends(require_admin)])
def sync_industries(after_id: int = 0, batch_size: int = 25, db: Session = Depends(get_db)):
    if after_id < 0 or batch_size < 1 or batch_size > 100:
        raise HTTPException(status_code=400, detail="after_id or batch_size is out of range")
    return sync_industry_batch(db, DartAdapter(settings.DART_API_KEY), after_id, batch_size)
