import secrets
from datetime import date

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.adapters.gdelt_news_adapter import GdeltNewsAdapter
from app.adapters.google_news_adapter import FallbackNewsAdapter, GoogleNewsRssAdapter
from app.config import settings
from app.db.session import get_db
from app.models.schema import BacktestRun
from app.services.news_candidate_backtest_service import NewsCandidateBacktestService
from app.services.news_candidate_validation_service import NewsCandidateValidationService
from app.services.news_classification_service import NewsClassificationService
from app.services.news_collection_service import NewsCollectionService
from app.services.news_stock_candidate_service import NewsStockCandidateService
from app.services.price_service import incremental_due_batch_update

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


def run_news_pipeline(db: Session):
    adapter = FallbackNewsAdapter(GdeltNewsAdapter(), GoogleNewsRssAdapter())
    collection = NewsCollectionService(adapter).sync(db, "24h", 100)
    classification = NewsClassificationService().classify_pending(db, 100, False)
    candidates = NewsStockCandidateService().generate(db, None, 100, 20, False)
    validation = NewsCandidateValidationService().validate(db)
    return {
        "collection": collection,
        "classification": classification,
        "candidates": candidates,
        "validation": validation,
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
    return result


@router.get("/news")
def collect_and_link_news(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """Collect, classify, link, and validate the active news window."""
    _authorize_cron(authorization)
    return run_news_pipeline(db)