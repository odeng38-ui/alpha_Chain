import secrets
from datetime import date

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.adapters.gdelt_news_adapter import GdeltNewsAdapter
from app.adapters.google_news_adapter import FallbackNewsAdapter, GoogleNewsRssAdapter
from app.adapters.us_market_adapter import YahooUSMarketAdapter
from app.config import settings
from app.db.session import get_db
from app.models.schema import BacktestRun, GlobalEvent
from app.services.audit_service import record_audit
from app.services.event_impact_backtest_service import EventImpactBacktestService
from app.services.event_impact_service import EventImpactV4Service
from app.services.news_candidate_backtest_service import NewsCandidateBacktestService
from app.services.news_candidate_validation_service import NewsCandidateValidationService
from app.services.news_classification_service import NewsClassificationService
from app.services.news_collection_service import NewsCollectionService
from app.services.news_stock_candidate_service import NewsStockCandidateService
from app.services.price_service import incremental_due_batch_update
from app.services.us_market_event_service import USMarketEventService

router = APIRouter(prefix="/cron", tags=["Cron"])


def _authorize_cron(authorization: str | None):
    if not settings.CRON_SECRET:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="CRON_SECRET is not configured",
        )

    expected = f"Bearer {settings.CRON_SECRET}"
    if authorization is None or not secrets.compare_digest(authorization, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid cron authorization",
        )


def _record_cron_success(db: Session, job_name: str):
    record_audit(
        db,
        actor="vercel-cron",
        action="completed",
        resource_type="cron_run",
        resource_id=job_name,
        before_state=None,
        after_state={"status": "SUCCESS"},
    )
    db.commit()


def run_us_market_pipeline(db: Session):
    sync = USMarketEventService(YahooUSMarketAdapter()).sync(db, lookback_days=180)
    generated = []
    failures = []
    for event_id in sync.get("event_ids", []):
        try:
            generated.append(EventImpactV4Service(5).generate(db, event_id, 20))
        except (LookupError, ValueError) as exc:
            failures.append({"event_id": event_id, "error": str(exc)})
    return {"sync": sync, "generated": generated, "failures": failures}


def run_weekly_event_impact_backtest(db: Session):
    iso_year, iso_week, _ = date.today().isocalendar()
    name = f"impact-v4-weekly-{iso_year}-W{iso_week:02d}"
    existing = db.query(BacktestRun).filter(BacktestRun.name == name).order_by(
        BacktestRun.id.desc(),
    ).first()
    if existing is not None:
        return {"run_id": existing.id, "status": existing.status, "created": False}
    event_count = db.query(GlobalEvent.id).filter(
        GlobalEvent.event_kind == "MARKET_SHOCK",
    ).count()
    if event_count < 10:
        return {"run_id": None, "status": "SKIPPED", "created": False,
                "reason": "MARKET_SHOCK_EVENTS_BELOW_10"}
    latest = db.query(BacktestRun).filter(
        BacktestRun.score_version == "impact-v4",
    ).order_by(BacktestRun.completed_at.desc(), BacktestRun.id.desc()).first()
    metadata = (latest.report or {}).get("model_metadata") if latest else {}
    baseline_run_id = (metadata or {}).get("baseline_run_id")
    run = EventImpactBacktestService("impact-v4").run(
        db, name, max_events=50, candidates_per_event=20,
        horizons=(1, 5), baseline_run_id=baseline_run_id,
    )
    return {"run_id": run.id, "status": run.status, "created": True,
            "baseline_run_id": baseline_run_id}


def run_news_pipeline(db: Session):
    adapter = FallbackNewsAdapter(GdeltNewsAdapter(), GoogleNewsRssAdapter())
    collection = NewsCollectionService(adapter).sync(db, "24h", 100)
    classification = NewsClassificationService().classify_pending(db, 100, False)
    candidates = NewsStockCandidateService().generate(db, None, 100, 20, False)
    validation = NewsCandidateValidationService().validate(db)
    us_market = run_us_market_pipeline(db)
    impact_backtest = run_weekly_event_impact_backtest(db)
    return {
        "collection": collection,
        "classification": classification,
        "candidates": candidates,
        "validation": validation,
        "us_market": us_market,
        "impact_backtest": impact_backtest,
    }


def run_daily_news_backtest(db: Session):
    name = f"news-candidate-daily-{date.today().isoformat()}"
    existing = db.query(BacktestRun).filter(BacktestRun.name == name).order_by(
        BacktestRun.id.desc(),
    ).first()
    if existing is not None:
        return {
            "run_id": existing.id,
            "status": existing.status,
            "created": False,
        }
    try:
        run = NewsCandidateBacktestService().run(
            db, name, horizons=(1, 5, 20), candidate_limit=1000,
        )
    except ValueError as exc:
        return {"run_id": None, "status": "SKIPPED", "reason": str(exc)}
    return {"run_id": run.id, "status": run.status, "created": True}

@router.get("/prices")
def collect_due_prices(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """Run one bounded daily-price batch from Vercel Cron."""
    _authorize_cron(authorization)
    result = incremental_due_batch_update(
        db,
        batch_size=max(1, min(settings.CRON_BATCH_SIZE, 100)),
    )
    result["news_backtest"] = run_daily_news_backtest(db)
    _record_cron_success(db, "prices")
    return result


@router.get("/news")
def collect_and_link_news(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """Collect, classify, link, and validate the active news window."""
    _authorize_cron(authorization)
    result = run_news_pipeline(db)
    _record_cron_success(db, "news")
    return result