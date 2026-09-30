from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config import settings
from app.db.session import get_db
from app.models.schema import Company, IdentifierMap
from app.services.master_batch import sync_provider_master
from app.services.master_service import CompanySecurityMasterService
from app.services.report_service import MappingReportService

router = APIRouter(prefix="/master", tags=["Master & Identification"])


@router.get("/companies")
def search_companies(
    query: Optional[str] = Query(None, description="기업명, 종목코드, corp_code, ISIN 검색어"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db)
):
    """
    기업 및 종목 식별자 통합 검색 API
    """
    db_query = db.query(Company)

    if query:
        q_str = query.strip()
        matching_company_ids = db.query(IdentifierMap.company_id).filter(
            IdentifierMap.source_id_value.ilike(f"%{q_str}%")
        )

        db_query = db_query.filter(
            or_(
                Company.name.ilike(f"%{q_str}%"),
                Company.corp_code.ilike(f"%{q_str}%"),
                Company.id.in_(matching_company_ids)
            )
        )


    companies = db_query.limit(limit).all()
    results = []
    for c in companies:
        securities_info = []
        for s in c.securities:
            securities_info.append({
                "security_id": s.id,
                "ticker": s.ticker,
                "market": s.market,
                "isin": s.isin,
                "security_type": s.security_type,
                "listed_at": s.listed_at.isoformat() if s.listed_at else None,
                "delisted_at": s.delisted_at.isoformat() if s.delisted_at else None,
                "effective_from": s.effective_from.isoformat() if s.effective_from else None,
                "effective_to": s.effective_to.isoformat() if s.effective_to else None,
            })

        id_maps = [
            {"source": im.source, "value": im.source_id_value, "is_primary": im.is_primary}
            for im in c.identifier_maps
        ]

        results.append({
            "company_id": c.id,
            "corp_code": c.corp_code,
            "name": c.name,
            "status": c.status,
            "industry_id": c.industry_id,
            "securities": securities_info,
            "identifier_maps": id_maps
        })

    return {"count": len(results), "data": results}


@router.get("/report")
def get_mapping_report(db: Session = Depends(get_db)):
    """
    기업-종목 식별자 매핑률 및 미매핑 리포트 조회
    """
    return MappingReportService.generate_mapping_report(db)


@router.post("/sync")
def sync_master_data(
    records: List[Dict[str, Any]],
    db: Session = Depends(get_db)
):
    """
    기업 및 종목 배치 적재 API
    """
    if not records:
        raise HTTPException(status_code=400, detail="레코드 목록이 비어 있습니다.")
    return CompanySecurityMasterService.sync_master_records(db, records)


@router.post("/sync-provider")
def sync_master_from_provider(
    offset: int = 0,
    batch_size: int = 200,
    db: Session = Depends(get_db),
):
    if offset < 0 or batch_size < 1 or batch_size > 500:
        raise HTTPException(status_code=400, detail="offset or batch_size is out of range")
    try:
        return sync_provider_master(
            db,
            settings.DART_API_KEY,
            krx_id=settings.KRX_ID,
            krx_pw=settings.KRX_PW,
            offset=offset,
            limit=batch_size,
        )
    except (ValueError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
