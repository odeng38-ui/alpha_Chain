"""
PriceService — 백필(backfill) 및 증분(incremental) 주가 수집 오케스트레이터.

핵심 원칙:
- UPSERT(INSERT OR IGNORE / ON CONFLICT DO NOTHING)로 중복 방지
- 체크포인트: 종목별 마지막 성공 날짜 추적 → 실패 후 중단 지점부터 재개
- 원본 해시(raw_hash) 저장으로 재현성 보존
"""

import logging
import os
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import and_, case, func, or_
from sqlalchemy.orm import Session, aliased

from app.adapters.base import AdapterError, BrokerAdapter, DataNotFoundError, OHLCVRecord
from app.adapters.pykrx_adapter import PykrxAdapter
from app.adapters.yahoo_adapter import YahooFinanceAdapter
from app.config import settings
from app.models.schema import (
    AuditLog,
    CollectionCheckpoint,
    DailyPrice,
    NewsArticle,
    NewsClassification,
    NewsStockCandidate,
    Security,
)

logger = logging.getLogger(__name__)

SEOUL_TIMEZONE = ZoneInfo("Asia/Seoul")
PRICE_DATA_READY_TIME = time(16, 30)
TRADING_DAY_LOOKBACK_DAYS = 14


class TargetTradeDateUnavailable(RuntimeError):
    pass


def determine_target_trade_date(
    adapter: BrokerAdapter,
    *,
    now: Optional[datetime] = None,
    market: str = "KOSPI",
    data_ready_time: time = PRICE_DATA_READY_TIME,
) -> date:
    """Return the latest closed Korean trading day with provider data expected."""
    current = now or datetime.now(SEOUL_TIMEZONE)
    if current.tzinfo is None:
        current = current.replace(tzinfo=SEOUL_TIMEZONE)
    else:
        current = current.astimezone(SEOUL_TIMEZONE)
    eligible_end = current.date()
    if current.time().replace(tzinfo=None) < data_ready_time:
        eligible_end -= timedelta(days=1)
    start = eligible_end - timedelta(days=TRADING_DAY_LOOKBACK_DAYS)
    try:
        trading_days = adapter.get_trading_days(start, eligible_end, market)
    except AdapterError as exc:
        raise TargetTradeDateUnavailable("trading calendar lookup failed") from exc
    candidates = [day for day in trading_days if start <= day <= eligible_end]
    if not candidates:
        raise TargetTradeDateUnavailable("no trading day found in lookup window")
    return max(candidates)


# 기본 어댑터 인스턴스 (싱글턴처럼 사용)
_default_adapter: Optional[BrokerAdapter] = None


def get_default_adapter() -> BrokerAdapter:
    global _default_adapter
    if _default_adapter is None:
        _default_adapter = YahooFinanceAdapter() if os.getenv("VERCEL") else PykrxAdapter()
    return _default_adapter

# ------------------------------------------------------------------ #
# 내부 헬퍼                                                             #
# ------------------------------------------------------------------ #

def _store_price_records(db: Session, security_id: int, records) -> tuple[int, int]:
    """Store a price batch using one duplicate lookup instead of one query per row."""
    if not records:
        return 0, 0
    trade_dates = {record.trade_date for record in records}
    existing_dates = {
        row[0]
        for row in db.query(DailyPrice.trade_date).filter(
            DailyPrice.security_id == security_id,
            DailyPrice.trade_date.in_(trade_dates),
        ).all()
    }
    inserted = 0
    seen_dates = set(existing_dates)
    for record in records:
        if record.trade_date in seen_dates:
            continue
        db.add(
            DailyPrice(
                security_id=security_id,
                trade_date=record.trade_date,
                open=record.open,
                high=record.high,
                low=record.low,
                close=record.close,
                volume=record.volume,
                value=record.value,
                adjusted_close=record.adjusted_close,
                raw_hash=record.raw_hash,
            )
        )
        seen_dates.add(record.trade_date)
        inserted += 1
    return inserted, len(records) - inserted


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


def import_price_records(
    db: Session,
    *,
    security_id: int,
    ticker: str,
    records: List[OHLCVRecord],
    source: str = "KRX_PYKRX",
) -> Dict[str, Any]:
    """Import a bounded, pre-fetched price batch with strict identity guards."""
    security = db.query(Security).filter(Security.id == security_id).first()
    if security is None:
        raise ValueError("security not found")
    if security.effective_to is not None:
        raise ValueError("security is not active")
    if security.ticker != ticker:
        raise ValueError("ticker does not match security")
    if not records:
        raise ValueError("at least one price record is required")
    if len(records) > 5000:
        raise ValueError("price import is limited to 5000 records")
    dates = [record.trade_date for record in records]
    if len(dates) != len(set(dates)):
        raise ValueError("duplicate trade dates are not allowed")
    if any(record.ticker != ticker for record in records):
        raise ValueError("record ticker does not match request ticker")
    if any(record.trade_date > date.today() for record in records):
        raise ValueError("future trade dates are not allowed")
    if any(record.close is None or record.close <= 0 for record in records):
        raise ValueError("every record must have a positive close")

    inserted, skipped = _store_price_records(db, security_id, records)
    last_trade_date = max(dates)
    _save_checkpoint(db, security_id, "SUCCESS", last_trade_date)
    db.add(AuditLog(
        actor="admin-api",
        action="import_prices",
        resource_type="security",
        resource_id=str(security_id),
        before_state={"ticker": ticker, "source": source},
        after_state={
            "inserted": inserted,
            "skipped": skipped,
            "first_trade_date": str(min(dates)),
            "last_trade_date": str(last_trade_date),
        },
        request_id=f"price-import-{security_id}-{last_trade_date}",
    ))
    db.commit()
    return {
        "security_id": security_id,
        "ticker": ticker,
        "source": source,
        "inserted": inserted,
        "skipped": skipped,
        "first_trade_date": str(min(dates)),
        "last_trade_date": str(last_trade_date),
    }

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

            inserted, skipped = _store_price_records(db, security_id, records)
            result.inserted += inserted
            result.skipped += skipped
            if records:
                result.last_success_date = records[-1].trade_date
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
    target_trade_date: Optional[date] = None,
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
    collection_end = target_trade_date or date.today()

    for sec in securities:
        last_date = _get_last_trade_date(db, sec.id)
        if last_date is None:
            # DB에 데이터 없음 → 승인 기준에 맞춰 최근 5년 백필
            start = collection_end - timedelta(days=settings.PRICE_INITIAL_LOOKBACK_DAYS)
        else:
            start = last_date + timedelta(days=1)

        if start > collection_end:
            logger.debug("[incremental] %s already current at %s", sec.ticker, last_date)
            if target_trade_date is not None:
                error = f"NO_DATA_FOR_TARGET_DATE:{collection_end.isoformat()}"
                result.errors.append(error)
                _save_checkpoint(db, sec.id, "FAILED", last_date, error)
                db.commit()
            continue

        try:
            records = adapter.fetch_ohlcv(
                ticker=sec.ticker,
                start_date=start,
                end_date=collection_end,
                market=sec.market,
            )

            inserted_count, skipped_count = _store_price_records(db, sec.id, records)
            latest_record_date = max(
                (record.trade_date for record in records),
                default=last_date,
            )
            if target_trade_date is not None and not any(
                record.trade_date == collection_end for record in records
            ):
                error = f"NO_DATA_FOR_TARGET_DATE:{collection_end.isoformat()}"
                result.errors.append(error)
                _save_checkpoint(db, sec.id, "FAILED", latest_record_date, error)
            else:
                _save_checkpoint(
                    db,
                    sec.id,
                    "SUCCESS",
                    latest_record_date or collection_end,
                )
            db.commit()
            result.updated_securities += 1
            result.total_inserted += inserted_count
            result.total_skipped += skipped_count

            logger.info(
                "[증분] %s: %s ~ %s → 적재 %d건",
                sec.ticker, start, collection_end, inserted_count,
            )

        except DataNotFoundError as exc:
            db.rollback()
            if target_trade_date is not None:
                error = f"NO_DATA_FOR_TARGET_DATE:{collection_end.isoformat()}"
                result.errors.append(error)
                _save_checkpoint(db, sec.id, "FAILED", last_date, error)
                db.commit()
                continue
            if last_date is not None:
                _save_checkpoint(db, sec.id, "SUCCESS", last_date)
                db.commit()
                result.updated_securities += 1
                logger.info(
                    "[incremental] %s: no new trading session after %s",
                    sec.ticker, last_date,
                )
                continue
            err_msg = f"security_id={sec.id} ticker={sec.ticker}: {exc}"
            result.errors.append(err_msg)
            _save_checkpoint(db, sec.id, "FAILED", error=err_msg)
            db.commit()

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

def active_news_candidate_security_ids(
    db: Session,
    *,
    lookback_days: int = 7,
) -> List[int]:
    """Return securities linked to recent directional news, without future data."""
    cutoff = datetime.combine(date.today() - timedelta(days=lookback_days), datetime.min.time())
    return [row[0] for row in db.query(NewsStockCandidate.security_id).join(
        NewsClassification,
        NewsClassification.id == NewsStockCandidate.classification_id,
    ).join(
        NewsArticle,
        NewsArticle.id == NewsClassification.news_article_id,
    ).filter(
        NewsStockCandidate.version == "news-link-v1",
        NewsStockCandidate.expected_direction.in_(("POSITIVE", "NEGATIVE")),
        NewsArticle.published_at >= cutoff,
    ).distinct().all()]

def price_collection_coverage(
    db: Session,
    *,
    target_trade_date: date,
    retry_now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Calculate active-common coverage from stored prices, not checkpoint success."""
    active_filter = (
        Security.security_type == "COMMON",
        Security.effective_to.is_(None),
    )
    active_count = db.query(func.count(Security.id)).filter(*active_filter).scalar() or 0
    completed_count = db.query(func.count(Security.id)).join(
        DailyPrice,
        and_(
            DailyPrice.security_id == Security.id,
            DailyPrice.trade_date == target_trade_date,
        ),
    ).filter(*active_filter).scalar() or 0
    missing_count = max(active_count - completed_count, 0)
    failed_base = db.query(func.count(Security.id)).join(
        CollectionCheckpoint,
        and_(
            CollectionCheckpoint.security_id == Security.id,
            CollectionCheckpoint.job_name == "daily_price",
        ),
    ).outerjoin(
        DailyPrice,
        and_(
            DailyPrice.security_id == Security.id,
            DailyPrice.trade_date == target_trade_date,
        ),
    ).filter(
        *active_filter,
        DailyPrice.security_id.is_(None),
        CollectionCheckpoint.status == "FAILED",
    )
    failed_count = failed_base.scalar() or 0
    retry_before = (retry_now or datetime.utcnow()) - timedelta(
        days=settings.PRICE_FAILURE_RETRY_DAYS
    )
    retry_waiting_count = failed_base.filter(
        CollectionCheckpoint.updated_at >= retry_before,
    ).scalar() or 0
    completion_rate = round((completed_count / active_count) * 100, 2) if active_count else 0.0
    return {
        "target_trade_date": target_trade_date.isoformat(),
        "active_count": active_count,
        "completed_count": completed_count,
        "missing_count": missing_count,
        "completion_rate": completion_rate,
        "failed_count": failed_count,
        "retry_waiting_count": retry_waiting_count,
    }


def incremental_due_batch_update(
    db: Session,
    *,
    batch_size: int = 20,
    adapter: Optional[BrokerAdapter] = None,
    target_trade_date: Optional[date] = None,
    attempted_after: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Process one bounded batch missing the target trading day's stored price."""
    if adapter is None:
        adapter = get_default_adapter()
    target = target_trade_date or determine_target_trade_date(adapter)
    retry_before = datetime.utcnow() - timedelta(days=settings.PRICE_FAILURE_RETRY_DAYS)
    target_price = aliased(DailyPrice)
    query = db.query(Security.id).outerjoin(
        CollectionCheckpoint,
        and_(
            CollectionCheckpoint.security_id == Security.id,
            CollectionCheckpoint.job_name == "daily_price",
        ),
    ).outerjoin(
        target_price,
        and_(
            target_price.security_id == Security.id,
            target_price.trade_date == target,
        ),
    ).filter(
        Security.security_type == "COMMON",
        Security.effective_to.is_(None),
        target_price.security_id.is_(None),
        or_(
            CollectionCheckpoint.security_id.is_(None),
            CollectionCheckpoint.status != "FAILED",
            and_(
                CollectionCheckpoint.status == "FAILED",
                CollectionCheckpoint.updated_at < retry_before,
            ),
        ),
    )
    if attempted_after is not None:
        query = query.filter(or_(
            CollectionCheckpoint.security_id.is_(None),
            CollectionCheckpoint.updated_at < attempted_after,
        ))
    total_candidates = query.count()
    priority_ids = active_news_candidate_security_ids(db)
    priority_order = case(
        (Security.id.in_(priority_ids), 0), else_=1,
    ) if priority_ids else Security.id
    missing_checkpoint_order = case(
        (CollectionCheckpoint.security_id.is_(None), 0), else_=1,
    )
    security_ids = [
        row[0]
        for row in query.order_by(
            missing_checkpoint_order,
            CollectionCheckpoint.updated_at,
            priority_order,
            Security.id,
        ).limit(batch_size).all()
    ]
    priority_set = set(priority_ids)
    result = incremental_update(
        db,
        security_ids=security_ids,
        adapter=adapter,
        target_trade_date=target,
    ).to_dict()
    processed = len(security_ids)
    return {
        **result,
        "target_trade_date": target.isoformat(),
        "batch_size": batch_size,
        "processed": processed,
        "total_candidates": total_candidates,
        "remaining": max(total_candidates - processed, 0),
        "security_ids": security_ids,
        "priority_candidates": len(priority_ids),
        "priority_processed": sum(item in priority_set for item in security_ids),
        "has_more": total_candidates > processed,
    }
