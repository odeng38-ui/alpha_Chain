"""
PriceService — 백필(backfill) 및 증분(incremental) 주가 수집 오케스트레이터.

핵심 원칙:
- UPSERT(INSERT OR IGNORE / ON CONFLICT DO NOTHING)로 중복 방지
- 체크포인트: 종목별 마지막 성공 날짜 추적 → 실패 후 중단 지점부터 재개
- 원본 해시(raw_hash) 저장으로 재현성 보존
"""

import logging
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.adapters.base import AdapterError, BrokerAdapter
from app.adapters.pykrx_adapter import PykrxAdapter
from app.models.schema import CollectionCheckpoint, DailyPrice, Security

logger = logging.getLogger(__name__)

# 기본 어댑터 인스턴스 (싱글턴처럼 사용)
_default_adapter: Optional[BrokerAdapter] = None


def get_default_adapter() -> BrokerAdapter:
    global _default_adapter
    if _default_adapter is None:
        _default_adapter = PykrxAdapter()
    return _default_adapter


# ------------------------------------------------------------------ #
# 내부 헬퍼                                                             #
# ------------------------------------------------------------------ #

def _upsert_price_record(db: Session, security_id: int, record) -> bool:
    """
    DailyPrice 단건 UPSERT.
    (security_id, trade_date) PK 충돌 시 무시(기존 데이터 보존).

    Returns:
        True if inserted, False if skipped (already exists)
    """
    existing = db.query(DailyPrice).filter(
        DailyPrice.security_id == security_id,
        DailyPrice.trade_date == record.trade_date,
    ).first()

    if existing:
        return False  # 이미 존재 → 스킵

    price = DailyPrice(
        security_id=security_id,
        trade_date=record.trade_date,
        open=record.open,
        high=record.high,
        low=record.low,
        close=record.close,
        volume=record.volume,
        value=record.value,
        adjusted_close=record.adjusted_close,
    )

    # raw_hash 필드가 있으면 설정
    if hasattr(DailyPrice, 'raw_hash') and record.raw_hash:
        price.raw_hash = record.raw_hash

    db.add(price)
    return True


def _get_last_trade_date(db: Session, security_id: int) -> Optional[date]:
    """종목의 DB 최신 거래일 조회."""
    result = db.query(func.max(DailyPrice.trade_date)).filter(
        DailyPrice.security_id == security_id
    ).scalar()
    return result


def _save_checkpoint(db: Session, security_id: int, status: str,
                     last_success_date: Optional[date] = None,
                     error: Optional[str] = None) -> None:
    checkpoint = db.query(CollectionCheckpoint).filter_by(
        job_name="daily_price", security_id=security_id
    ).first()
    if checkpoint is None:
        checkpoint = CollectionCheckpoint(job_name="daily_price", security_id=security_id)
        db.add(checkpoint)
    checkpoint.status = status
    checkpoint.last_error = error
    if last_success_date is not None:
        checkpoint.last_success_date = last_success_date


# ------------------------------------------------------------------ #
# 백필 (Backfill)                                                       #
# ------------------------------------------------------------------ #

class BackfillResult:
    def __init__(self, security_id: int, ticker: str):
        self.security_id = security_id
        self.ticker = ticker
        self.inserted = 0
        self.skipped = 0
        self.errors: List[str] = []
        self.last_success_date: Optional[date] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "security_id": self.security_id,
            "ticker": self.ticker,
            "inserted": self.inserted,
            "skipped": self.skipped,
            "errors": self.errors,
            "last_success_date": str(self.last_success_date) if self.last_success_date else None,
        }


def backfill_security(
    db: Session,
    security_id: int,
    start_date: date,
    end_date: date,
    adapter: Optional[BrokerAdapter] = None,
    chunk_days: int = 90,
) -> BackfillResult:
    """
    단일 종목 기간 백필.

    Args:
        db: DB 세션
        security_id: 수집할 Security ID
        start_date: 시작일
        end_date: 종료일
        adapter: 시세 어댑터 (None이면 기본 pykrx 사용)
        chunk_days: 한 번에 가져올 기간 (일). API 호출 제한 대비 분할 수집.

    Returns:
        BackfillResult
    """
    if adapter is None:
        adapter = get_default_adapter()

    security = db.query(Security).filter(Security.id == security_id).first()
    if not security:
        raise ValueError(f"Security ID {security_id} 를 찾을 수 없습니다.")

    result = BackfillResult(security_id=security_id, ticker=security.ticker)

    # 청크 단위로 분할 수집 (체크포인트 기반)
    current_start = start_date
    while current_start <= end_date:
        current_end = min(current_start + timedelta(days=chunk_days - 1), end_date)

        try:
            records = adapter.fetch_ohlcv(
                ticker=security.ticker,
                start_date=current_start,
                end_date=current_end,
                market=security.market,
            )

            for rec in records:
                inserted = _upsert_price_record(db, security_id, rec)
                if inserted:
                    result.inserted += 1
                    result.last_success_date = rec.trade_date
                else:
                    result.skipped += 1

            db.commit()
            _save_checkpoint(db, security_id, "SUCCESS", current_end)
            db.commit()
            logger.info(
                "[백필] %s (%s) %s~%s: 적재 %d건, 스킵 %d건",
                security.ticker, security_id,
                current_start, current_end,
                result.inserted, result.skipped,
            )

        except AdapterError as exc:
            db.rollback()
            err_msg = f"{current_start}~{current_end} 수집 실패: {exc}"
            result.errors.append(err_msg)
            logger.error("[백필] %s %s", security.ticker, err_msg)
            _save_checkpoint(db, security_id, "FAILED", error=err_msg)
            db.commit()
            break

        current_start = current_end + timedelta(days=1)

    return result


def backfill_batch(
    db: Session,
    security_ids: List[int],
    start_date: date,
    end_date: date,
    adapter: Optional[BrokerAdapter] = None,
) -> List[Dict[str, Any]]:
    """
    여러 종목 배치 백필.

    Returns:
        각 종목의 BackfillResult.to_dict() 리스트
    """
    results = []
    total = len(security_ids)
    for idx, sid in enumerate(security_ids, 1):
        logger.info("[배치 백필] %d/%d — security_id=%d", idx, total, sid)
        try:
            res = backfill_security(db, sid, start_date, end_date, adapter)
            results.append(res.to_dict())
        except Exception as exc:
            results.append({
                "security_id": sid,
                "error": str(exc),
            })
    return results


# ------------------------------------------------------------------ #
# 증분 수집 (Incremental Update)                                        #
# ------------------------------------------------------------------ #

class IncrementalResult:
    def __init__(self):
        self.updated_securities = 0
        self.total_inserted = 0
        self.total_skipped = 0
        self.errors: List[str] = []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "updated_securities": self.updated_securities,
            "total_inserted": self.total_inserted,
            "total_skipped": self.total_skipped,
            "errors": self.errors,
        }


def incremental_update(
    db: Session,
    security_ids: Optional[List[int]] = None,
    adapter: Optional[BrokerAdapter] = None,
) -> IncrementalResult:
    """
    증분 수집: 각 종목의 마지막 적재일 이후 데이터만 수집.

    Args:
        db: DB 세션
        security_ids: 수집할 종목 ID 목록 (None이면 전체 활성 COMMON 종목)
        adapter: 시세 어댑터

    Returns:
        IncrementalResult
    """
    if adapter is None:
        adapter = get_default_adapter()

    if security_ids is None:
        securities = db.query(Security).filter(
            Security.security_type == "COMMON",
            Security.effective_to.is_(None),
        ).all()
    else:
        securities = db.query(Security).filter(Security.id.in_(security_ids)).all()

    result = IncrementalResult()
    today = date.today()

    for sec in securities:
        last_date = _get_last_trade_date(db, sec.id)
        if last_date is None:
            # DB에 데이터 없음 → 승인 기준에 맞춰 최근 5년 백필
            start = today - timedelta(days=365 * 5)
        else:
            start = last_date + timedelta(days=1)

        if start > today:
            logger.debug(
                "[증분] %s — 이미 최신 (last_date=%s)", sec.ticker, last_date
            )
            continue

        try:
            records = adapter.fetch_ohlcv(
                ticker=sec.ticker,
                start_date=start,
                end_date=today,
                market=sec.market,
            )

            inserted_count = 0
            skipped_count = 0
            for rec in records:
                ins = _upsert_price_record(db, sec.id, rec)
                if ins:
                    inserted_count += 1
                else:
                    skipped_count += 1

            db.commit()
            _save_checkpoint(db, sec.id, "SUCCESS", today)
            db.commit()
            result.updated_securities += 1
            result.total_inserted += inserted_count
            result.total_skipped += skipped_count

            logger.info(
                "[증분] %s: %s ~ %s → 적재 %d건",
                sec.ticker, start, today, inserted_count,
            )

        except AdapterError as exc:
            db.rollback()
            err_msg = f"security_id={sec.id} ticker={sec.ticker}: {exc}"
            result.errors.append(err_msg)
            logger.error("[증분] 수집 실패: %s", err_msg)
            _save_checkpoint(db, sec.id, "FAILED", error=err_msg)
            db.commit()

    return result

class IncrementalBatchResult:
    def __init__(self, *, offset: int, batch_size: int, total_candidates: int, security_ids: List[int], result: IncrementalResult):
        self.offset = offset
        self.batch_size = batch_size
        self.total_candidates = total_candidates
        self.security_ids = security_ids
        self.result = result

    def to_dict(self) -> Dict[str, Any]:
        processed = len(self.security_ids)
        next_offset = self.offset + processed
        return {
            **self.result.to_dict(),
            "offset": self.offset,
            "batch_size": self.batch_size,
            "processed": processed,
            "total_candidates": self.total_candidates,
            "security_ids": self.security_ids,
            "next_offset": next_offset if next_offset < self.total_candidates else None,
            "has_more": next_offset < self.total_candidates,
        }


def incremental_batch_update(
    db: Session,
    *,
    offset: int = 0,
    batch_size: int = 20,
    failed_only: bool = False,
    adapter: Optional[BrokerAdapter] = None,
) -> IncrementalBatchResult:
    """Run a bounded incremental batch, optionally limited to failed checkpoints."""
    query = db.query(Security.id).filter(
        Security.security_type == "COMMON",
        Security.effective_to.is_(None),
    )
    if failed_only:
        query = query.join(
            CollectionCheckpoint,
            (CollectionCheckpoint.security_id == Security.id)
            & (CollectionCheckpoint.job_name == "daily_price"),
        ).filter(CollectionCheckpoint.status == "FAILED")

    total_candidates = query.count()
    security_ids = [row[0] for row in query.order_by(Security.id).offset(offset).limit(batch_size).all()]
    result = incremental_update(db, security_ids=security_ids, adapter=adapter)
    return IncrementalBatchResult(
        offset=offset,
        batch_size=batch_size,
        total_candidates=total_candidates,
        security_ids=security_ids,
        result=result,
    )
