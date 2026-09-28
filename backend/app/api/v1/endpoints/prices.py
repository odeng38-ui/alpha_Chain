"""
가격(일봉) 관련 API 엔드포인트.

경로:
    GET  /api/v1/prices/{security_id}          — 기간별 일봉 조회
    POST /api/v1/prices/backfill               — 백필 수집 트리거
    POST /api/v1/prices/incremental            — 증분 수집 트리거
    GET  /api/v1/prices/quality                — 전체 품질 대시보드
    GET  /api/v1/prices/{security_id}/quality  — 단일 종목 품질 리포트
"""

from datetime import date, timedelta
from typing import List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.jobs import price_collection_runner
from app.models.schema import CollectionCheckpoint, DailyPrice, Security
from app.services import price_quality, price_service

router = APIRouter(prefix="/prices", tags=["Prices"])


# ------------------------------------------------------------------ #
# 요청/응답 모델                                                          #
# ------------------------------------------------------------------ #

class BackfillRequest(BaseModel):
    security_ids: List[int] = Field(..., description="수집할 Security ID 목록")
    start_date: date = Field(..., description="백필 시작일 (YYYY-MM-DD)")
    end_date: date = Field(default_factory=date.today, description="백필 종료일 (기본: 오늘)")


class IncrementalRequest(BaseModel):
    security_ids: Optional[List[int]] = None
    offset: int = Field(default=0, ge=0)
    batch_size: int = Field(default=20, ge=1, le=100)
    failed_only: bool = False

# ------------------------------------------------------------------ #
# 엔드포인트                                                             #
# ------------------------------------------------------------------ #

class BackgroundCollectionRequest(BaseModel):
    batch_size: int = Field(default=20, ge=1, le=100)

@router.get("/quality", summary="전체 품질 대시보드")
def get_quality_dashboard(
    limit: int = Query(5000, ge=1, le=10000, description="검사할 최대 종목 수"),
    db: Session = Depends(get_db),
):
    """
    보유 종목의 일봉 데이터 품질을 일괄 검사합니다.

    - **중복**: (security_id, trade_date) 단위 중복 건수
    - **결측**: 실제 거래일 대비 적재율 (%)
    - **급변**: 전일 대비 ±30% 초과 가격
    - **음수 거래량**: volume < 0 건수
    - **수정주가 불일치**: adjusted_close / close 비율 이상
    """
    svc = price_quality.PriceQualityService()
    return svc.check_all(db, limit=limit)


@router.get("/collection-status", summary="Price collection checkpoint status")
def get_collection_status(db: Session = Depends(get_db)):
    total = db.query(func.count(Security.id)).filter(
        Security.security_type == "COMMON",
        Security.effective_to.is_(None),
    ).scalar() or 0
    rows = db.query(CollectionCheckpoint.status, func.count(CollectionCheckpoint.security_id)).filter(
        CollectionCheckpoint.job_name == "daily_price",
    ).group_by(CollectionCheckpoint.status).all()
    counts = {status.lower(): count for status, count in rows}
    checkpointed = sum(counts.values())
    ordered_ids = [row[0] for row in db.query(Security.id).filter(
        Security.security_type == "COMMON",
        Security.effective_to.is_(None),
    ).order_by(Security.id).all()]
    attempted_ids = {row[0] for row in db.query(CollectionCheckpoint.security_id).filter(
        CollectionCheckpoint.job_name == "daily_price",
        CollectionCheckpoint.status.in_(("SUCCESS", "FAILED")),
    ).all()}
    next_offset = next((index for index, security_id in enumerate(ordered_ids) if security_id not in attempted_ids), len(ordered_ids))
    failures = db.query(CollectionCheckpoint, Security).join(
        Security, Security.id == CollectionCheckpoint.security_id,
    ).filter(
        CollectionCheckpoint.job_name == "daily_price",
        CollectionCheckpoint.status == "FAILED",
    ).order_by(CollectionCheckpoint.updated_at.desc()).limit(20).all()
    return {
        "total": total,
        "success": counts.get("success", 0),
        "failed": counts.get("failed", 0),
        "pending": max(total - checkpointed, 0) + counts.get("pending", 0),
        "next_offset": next_offset,
        "failures": [{
            "security_id": checkpoint.security_id,
            "ticker": security.ticker,
            "error": checkpoint.last_error,
            "updated_at": checkpoint.updated_at,
        } for checkpoint, security in failures],
    }

@router.post("/collection-run/start", summary="Start or resume background price collection")
def start_background_collection(req: BackgroundCollectionRequest):
    return price_collection_runner.start_collection(req.batch_size)


@router.post("/collection-run/stop", summary="Stop background price collection")
def stop_background_collection():
    return price_collection_runner.stop_collection()


@router.get("/collection-run/status", summary="Background price collection status")
def background_collection_status():
    return price_collection_runner.runner_status()

@router.get("/{security_id}/quality", summary="단일 종목 품질 리포트")
def get_security_quality(
    security_id: int,
    start_date: Optional[date] = Query(None, description="검사 시작일"),
    end_date: Optional[date] = Query(None, description="검사 종료일"),
    db: Session = Depends(get_db),
):
    """지정 종목의 일봉 데이터 품질을 상세 검사합니다."""
    svc = price_quality.PriceQualityService()
    try:
        report = svc.check_security(db, security_id, start_date, end_date)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return report.to_dict()


@router.get("/{security_id}", summary="기간별 일봉 조회")
def get_prices(
    security_id: int,
    start_date: Optional[date] = Query(
        None,
        description="조회 시작일 (기본: 30일 전)",
    ),
    end_date: Optional[date] = Query(
        None,
        description="조회 종료일 (기본: 오늘)",
    ),
    db: Session = Depends(get_db),
):
    """
    지정 종목의 일봉 OHLCV를 반환합니다.

    - **수정주가(adjusted_close)**: 분할·배당 조정 완료된 종가
    - **close**: 원시(비조정) 종가
    - **시장 휴장일**은 데이터가 없으며 결과에서 자동 제외됩니다.
    - **기준 시각**: KST 기준 거래일 (trade_date)
    """
    security = db.query(Security).filter(Security.id == security_id).first()
    if not security:
        raise HTTPException(status_code=404, detail=f"Security ID {security_id} 를 찾을 수 없습니다.")

    today = date.today()
    if end_date is None:
        end_date = today
    if start_date is None:
        start_date = today - timedelta(days=30)

    prices = (
        db.query(DailyPrice)
        .filter(
            DailyPrice.security_id == security_id,
            DailyPrice.trade_date >= start_date,
            DailyPrice.trade_date <= end_date,
        )
        .order_by(DailyPrice.trade_date)
        .all()
    )

    return {
        "security_id": security_id,
        "ticker": security.ticker,
        "market": security.market,
        "note": "adjusted_close는 수정주가, close는 원시(비조정) 종가입니다. 타임존: KST.",
        "start_date": str(start_date),
        "end_date": str(end_date),
        "count": len(prices),
        "data": [
            {
                "trade_date": str(p.trade_date),
                "open": float(p.open) if p.open is not None else None,
                "high": float(p.high) if p.high is not None else None,
                "low": float(p.low) if p.low is not None else None,
                "close": float(p.close) if p.close is not None else None,
                "volume": p.volume,
                "value": float(p.value) if p.value is not None else None,
                "adjusted_close": float(p.adjusted_close) if p.adjusted_close is not None else None,
            }
            for p in prices
        ],
    }


@router.post("/backfill", summary="백필 수집 트리거")
def trigger_backfill(
    req: BackfillRequest,
    db: Session = Depends(get_db),
):
    """
    지정 종목(들)의 기간별 백필 수집을 즉시 실행합니다.

    - 동일 날짜 데이터가 이미 있으면 **스킵** (중복 방지)
    - 부분 실패 시 해당 구간을 `errors`에 기록하고 계속 진행
    - 전체 종목 5년치 백필은 시간이 오래 걸릴 수 있습니다 (pykrx rate limit 고려)
    """
    if req.start_date > req.end_date:
        raise HTTPException(status_code=400, detail="start_date는 end_date보다 이전이어야 합니다.")

    results = price_service.backfill_batch(
        db=db,
        security_ids=req.security_ids,
        start_date=req.start_date,
        end_date=req.end_date,
    )
    return {"status": "completed", "results": results}


@router.post("/incremental", summary="증분 수집 트리거")
def trigger_incremental(
    req: IncrementalRequest = Body(default=IncrementalRequest()),
    db: Session = Depends(get_db),
):
    """
    각 종목의 마지막 적재일 이후 데이터를 수집합니다.

    - `security_ids`를 생략하면 전체 활성 COMMON 종목을 대상으로 합니다.
    - 스케줄러(장 마감 후 16:30 KST)에서 자동 실행되기도 합니다.
    """
    if req.security_ids is not None:
        result = price_service.incremental_update(db=db, security_ids=req.security_ids)
        return {"status": "completed", **result.to_dict(), "processed": len(req.security_ids), "has_more": False, "next_offset": None}
    batch = price_service.incremental_batch_update(
        db,
        offset=req.offset,
        batch_size=req.batch_size,
        failed_only=req.failed_only,
    )
    return {"status": "completed", **batch.to_dict()}
