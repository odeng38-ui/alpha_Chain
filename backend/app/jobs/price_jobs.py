"""
APScheduler 기반 가격 수집 스케줄러.

스케줄:
- 증분 수집: 매 영업일 16:30 KST (장 마감 약 30분 후)
- 주말/공휴일은 pykrx가 알아서 빈 결과를 반환하므로 별도 처리 불필요

사용법:
    from app.jobs.price_jobs import get_scheduler
    scheduler = get_scheduler()
    scheduler.start()   # 앱 시작 시
    scheduler.shutdown()  # 앱 종료 시
"""

import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.db.session import SessionLocal
from app.services import price_service

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def run_incremental_job():
    """증분 수집 잡 (스케줄러에서 호출)."""
    logger.info("[스케줄러] 증분 수집 시작: %s", datetime.now().isoformat())
    db = SessionLocal()
    try:
        result = price_service.incremental_update(db=db)
        logger.info(
            "[스케줄러] 증분 수집 완료 — 종목: %d, 적재: %d, 오류: %d",
            result.updated_securities,
            result.total_inserted,
            len(result.errors),
        )
        if result.errors:
            for err in result.errors:
                logger.warning("[스케줄러] 오류: %s", err)
    except Exception as exc:
        logger.error("[스케줄러] 증분 수집 실패: %s", exc, exc_info=True)
    finally:
        db.close()


def get_scheduler() -> BackgroundScheduler:
    """스케줄러 싱글턴 반환."""
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler(timezone="Asia/Seoul")

        # 매 영업일 16:30 KST 증분 수집
        # (월~금, 시간: 16:30 KST)
        _scheduler.add_job(
            run_incremental_job,
            trigger=CronTrigger(
                day_of_week="mon-fri",
                hour=16,
                minute=30,
                timezone="Asia/Seoul",
            ),
            id="incremental_price_update",
            name="일봉 증분 수집 (장 마감 후)",
            replace_existing=True,
        )

        logger.info("[스케줄러] 증분 수집 잡 등록 완료 (매 영업일 16:30 KST)")

    return _scheduler
