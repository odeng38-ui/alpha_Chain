from datetime import date
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, collate, func, or_, text
from sqlalchemy.orm import Session, load_only

from app.adapters.krx_listing_provider import KrxListingProvider
from app.config import settings
from app.db.session import get_db
from app.models.schema import Company, IdentifierMap, Security
from app.security import require_admin
from app.services.master_batch import sync_provider_master
from app.services.master_service import CompanySecurityMasterService
from app.services.report_service import MappingReportService

router = APIRouter(prefix="/master", tags=["Master & Identification"])


_korean_collation_cache: Optional[str] = None


def get_korean_collation(db: Session) -> Optional[str]:
    """PostgreSQL에서 사용 가능한 한국어 Collation을 조회하여 캐시.
    미지원 환경에서는 None을 반환하여 안전하게 폴백.
    """
    global _korean_collation_cache
    if _korean_collation_cache is not None:
        return _korean_collation_cache or None
    try:
        bind = db.get_bind()
        if bind is not None and bind.dialect.name == "postgresql":
            row = db.execute(
                text(
                    "SELECT collname FROM pg_collation "
                    "WHERE collname IN ('ko-KR-x-icu', 'ko-x-icu', 'ko_KR.utf8', 'ko_KR.UTF-8') "
                    "ORDER BY CASE collname "
                    "WHEN 'ko-KR-x-icu' THEN 1 "
                    "WHEN 'ko-x-icu' THEN 2 "
                    "WHEN 'ko_KR.utf8' THEN 3 "
                    "ELSE 4 END LIMIT 1;"
                )
            ).scalar()
            _korean_collation_cache = str(row) if row else ""
        else:
            _korean_collation_cache = ""
    except Exception:
        _korean_collation_cache = ""
    return _korean_collation_cache or None


def classify_security_listing_status(s: Security, company_name: Optional[str] = None) -> str:
    # 1. Official KRX KIND provider verification
    provider = KrxListingProvider.get_instance()
    if provider.is_available:
        c_name = company_name or (s.company.name if s.company else None)
        v = provider.verify_security(s.ticker, company_name=c_name, db_delisted_at=s.delisted_at)
        return v.status

    # 2. Fallback when official provider is unavailable (safe fallback, never assume LISTED)
    today = date.today()
    if s.delisted_at is not None and s.delisted_at <= today:
        return "DELISTED"
    if s.effective_to is not None and s.effective_to <= today and s.delisted_at is None:
        return "UNKNOWN"
    if s.listed_at is not None and s.listed_at <= today and s.delisted_at is None:
        return "LISTED"
    return "UNKNOWN"
def sort_securities_for_company(securities: List[Security], company_name: Optional[str] = None) -> List[Security]:
    def sec_sort_key(s: Security):
        st = classify_security_listing_status(s, company_name=company_name)
        priority = 3
        if st == "LISTED" and s.security_type == "COMMON":
            priority = 0
        elif st == "LISTED":
            priority = 1
        elif st == "UNKNOWN":
            priority = 2
        elif st == "DELISTED":
            priority = 3
        return (priority, s.id)

    return sorted(securities, key=sec_sort_key)


@router.get("/companies")
def search_companies(
    query: Optional[str] = Query(None, description="기업명, 종목코드, corp_code, ISIN 검색어"),
    market: str = Query("ALL", description="ALL, KOSPI, KOSDAQ, KONEX"),
    status: str = Query("ALL", description="ALL, LISTED, DELISTED, UNLISTED, UNKNOWN"),
    sort_by: str = Query("name", description="name(기업명순), ticker(종목코드순)"),
    sort_order: str = Query("asc", description="asc, desc"),
    page: int = Query(1, ge=1, description="페이지 번호"),
    page_size: Optional[int] = Query(None, ge=1, le=200, description="페이지 크기"),
    limit: Optional[int] = Query(None, ge=1, le=200, description="기존 limit 파라미터 하위 호환용"),
    db: Session = Depends(get_db)
):
    """
    기업 및 종목 식별자 통합 검색, 정렬 및 페이지네이션 API
    """
    effective_limit = page_size if page_size is not None else (limit if limit is not None else 50)
    offset = (page - 1) * effective_limit

    # Base query with load_only for unmigrated columns safety
    base_query = db.query(Company).options(
        load_only(Company.id, Company.corp_code, Company.name, Company.status, Company.industry_id)
    )
    count_query = db.query(func.count(Company.id))

    # 1. Search Query
    if query and query.strip():
        q = query.strip()
        matched_im = db.query(IdentifierMap.company_id).filter(IdentifierMap.source_id_value.ilike(f"%{q}%"))
        matched_sec = db.query(Security.company_id).filter(Security.ticker.ilike(f"%{q}%"))
        search_filter = or_(
            Company.name.ilike(f"%{q}%"),
            Company.corp_code.ilike(f"%{q}%"),
            Company.id.in_(matched_im),
            Company.id.in_(matched_sec)
        )
        base_query = base_query.filter(search_filter)
        count_query = count_query.filter(search_filter)

    # 2. Market filter
    if market and market.upper() != "ALL":
        m = market.upper()
        comp_with_mkt = db.query(Security.company_id).filter(Security.market == m)
        base_query = base_query.filter(Company.id.in_(comp_with_mkt))
        count_query = count_query.filter(Company.id.in_(comp_with_mkt))

    # 3. Status filter
    active_comp_ids = db.query(Security.company_id).filter(
        Security.listed_at.isnot(None),
        Security.delisted_at.is_(None)
    )
    delisted_comp_ids = db.query(Security.company_id).filter(
        Security.delisted_at.isnot(None),
        ~Security.company_id.in_(active_comp_ids)
    )
    unknown_comp_ids = db.query(Security.company_id).filter(
        Security.listed_at.is_(None),
        Security.delisted_at.is_(None),
        ~Security.company_id.in_(active_comp_ids),
        ~Security.company_id.in_(delisted_comp_ids)
    )

    if status and status.upper() != "ALL":
        st = status.upper()
        if st == "LISTED":
            base_query = base_query.filter(Company.id.in_(active_comp_ids))
            count_query = count_query.filter(Company.id.in_(active_comp_ids))
        elif st == "DELISTED":
            base_query = base_query.filter(Company.id.in_(delisted_comp_ids))
            count_query = count_query.filter(Company.id.in_(delisted_comp_ids))
        elif st == "UNKNOWN":
            base_query = base_query.filter(Company.id.in_(unknown_comp_ids))
            count_query = count_query.filter(Company.id.in_(unknown_comp_ids))
        elif st == "UNLISTED":
            comp_with_sec = db.query(Security.company_id)
            base_query = base_query.filter(~Company.id.in_(comp_with_sec))
            count_query = count_query.filter(~Company.id.in_(comp_with_sec))

    # 4. Sorting
    order_expressions = []
    # If status is ALL, LISTED companies come first!
    if status.upper() == "ALL":
        is_active_subq = db.query(Security.id).filter(
            Security.company_id == Company.id,
            Security.listed_at.isnot(None),
            Security.delisted_at.is_(None)
        ).exists()
        order_expressions.append(case((is_active_subq, 0), else_=1))

    if sort_by.lower() == "ticker":
        sec_ticker_subq = db.query(Security.ticker).filter(
            Security.company_id == Company.id
        ).order_by(
            case((Security.listed_at.isnot(None) & Security.delisted_at.is_(None), 0), else_=1),
            Security.id.asc()
        ).limit(1).scalar_subquery()

        if sort_order.lower() == "desc":
            order_expressions.append(sec_ticker_subq.desc())
        else:
            order_expressions.append(sec_ticker_subq.asc())
    else:
        # Korean Hangul (0) -> English Alphabet (1) -> Other symbols/numbers (2)
        bind = db.get_bind()
        is_pg = bind is not None and bind.dialect.name == "postgresql"
        if is_pg:
            name_group = case(
                (Company.name.op("~")("^[가-힣ㄱ-ㅎ]"), 0),
                (Company.name.op("~")("^[A-Za-z]"), 1),
                else_=2,
            )
        else:
            first_char = func.substr(Company.name, 1, 1)
            first_lower = func.lower(first_char)
            name_group = case(
                ((first_char >= "가") & (first_char <= "힣"), 0),
                ((first_lower >= "a") & (first_lower <= "z"), 1),
                else_=2,
            )

        k_coll = get_korean_collation(db) if is_pg else None
        name_col = collate(Company.name, k_coll) if k_coll else Company.name

        if sort_order.lower() == "desc":
            order_expressions.append(name_group.desc())
            order_expressions.append(name_col.desc())
        else:
            order_expressions.append(name_group.asc())
            order_expressions.append(name_col.asc())

    # Stable tie breaker
    order_expressions.append(Company.id.asc())
    base_query = base_query.order_by(*order_expressions)

    total_count = count_query.scalar() or 0
    companies = base_query.offset(offset).limit(effective_limit).all()
    total_pages = (total_count + effective_limit - 1) // effective_limit if total_count > 0 else 0
    has_more = page < total_pages

    provider = KrxListingProvider.get_instance()
    source_name = provider.source_name if provider.is_available else "NONE"
    as_of_val = provider.as_of_date if provider.is_available else None

    results = []
    for c in companies:
        sorted_securities = sort_securities_for_company(c.securities, company_name=c.name)
        primary_status = classify_security_listing_status(sorted_securities[0], company_name=c.name) if sorted_securities else "UNLISTED"

        securities_info = []
        for idx, s in enumerate(sorted_securities):
            s_status = classify_security_listing_status(s, company_name=c.name)
            securities_info.append({
                "security_id": s.id,
                "ticker": s.ticker,
                "market": s.market,
                "isin": s.isin,
                "security_type": s.security_type,
                "listing_status": s_status,
                "listing_source": source_name,
                "listing_as_of": as_of_val,
                "is_primary": (idx == 0),
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
            "listing_status": primary_status,
            "industry_id": c.industry_id,
            "securities": securities_info,
            "identifier_maps": id_maps
        })

    return {
        "count": len(results),
        "total_count": total_count,
        "page": page,
        "page_size": effective_limit,
        "total_pages": total_pages,
        "has_more": has_more,
        "listing_source": source_name,
        "listing_as_of": as_of_val,
        "data": results
    }



@router.get("/report")
def get_mapping_report(db: Session = Depends(get_db)):
    """
    기업-종목 식별자 매핑률 및 미매핑 리포트 조회
    """
    return MappingReportService.generate_mapping_report(db)


@router.post("/sync", dependencies=[Depends(require_admin)])
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


@router.post("/sync-provider", dependencies=[Depends(require_admin)])
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
