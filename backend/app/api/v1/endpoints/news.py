from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.adapters.gdelt_news_adapter import GdeltNewsAdapter
from app.adapters.google_news_adapter import FallbackNewsAdapter, GoogleNewsRssAdapter
from app.db.session import get_db
from app.models.schema import (
    BacktestRun,
    Company,
    NewsArticle,
    NewsCandidateValidationRun,
    NewsClassification,
    NewsStockCandidate,
    Security,
)
from app.security import require_admin
from app.services.news_candidate_validation_service import NewsCandidateValidationService
from app.services.news_classification_service import NewsClassificationService
from app.services.news_collection_service import NewsCollectionService
from app.services.news_stock_candidate_service import NewsStockCandidateService

router = APIRouter(prefix="/news", tags=["News"])


@router.post("/sync", dependencies=[Depends(require_admin)])
def sync_news(timespan: str = Query("24h", pattern=r"^[1-9][0-9]*(min|h|d|w)$"),
              max_records: int = Query(100, ge=1, le=250),
              db: Session = Depends(get_db)):
    adapter = FallbackNewsAdapter(GdeltNewsAdapter(), GoogleNewsRssAdapter())
    return NewsCollectionService(adapter).sync(db, timespan, max_records)


@router.get("/articles")
def list_news(domain: str | None = None,
              limit: int = Query(100, ge=1, le=500),
              db: Session = Depends(get_db)):
    query = db.query(NewsArticle)
    if domain:
        query = query.filter(NewsArticle.domain == domain)
    rows = query.order_by(NewsArticle.published_at.desc()).limit(limit).all()
    return {"count": len(rows), "data": [{
        "id": row.id, "source": row.source, "title": row.title,
        "url": row.url, "domain": row.domain, "language": row.language,
        "source_country": row.source_country,
        "published_at": row.published_at.isoformat(), "image_url": row.image_url,
    } for row in rows]}

@router.post("/classify", dependencies=[Depends(require_admin)])
def classify_news(limit: int = Query(100, ge=1, le=500),
                  reclassify: bool = False,
                  db: Session = Depends(get_db)):
    return NewsClassificationService().classify_pending(db, limit, reclassify)


@router.get("/classifications")
def list_classifications(event_kind: str | None = None,
                         direction: str | None = None,
                         review_required: bool | None = None,
                         limit: int = Query(100, ge=1, le=500),
                         db: Session = Depends(get_db)):
    query = db.query(NewsClassification, NewsArticle).join(
        NewsArticle, NewsArticle.id == NewsClassification.news_article_id,
    )
    if event_kind:
        query = query.filter(NewsClassification.event_kind == event_kind.upper())
    if direction:
        query = query.filter(NewsClassification.direction == direction.upper())
    if review_required is not None:
        query = query.filter(NewsClassification.review_required == review_required)
    rows = query.order_by(NewsArticle.published_at.desc()).limit(limit).all()
    return {"count": len(rows), "data": [{
        "article_id": article.id, "title": article.title, "url": article.url,
        "published_at": article.published_at.isoformat(),
        "event_kind": item.event_kind, "industries": item.industries,
        "direction": item.direction, "confidence": item.confidence,
        "matched_keywords": item.matched_keywords,
        "rationale": item.rationale, "review_required": item.review_required,
        "version": item.version,
    } for item, article in rows]}

@router.post("/stock-candidates/generate", dependencies=[Depends(require_admin)])
def generate_stock_candidates(article_id: int | None = None,
                              classification_limit: int = Query(100, ge=1, le=500),
                              candidates_per_article: int = Query(20, ge=1, le=100),
                              regenerate: bool = False,
                              db: Session = Depends(get_db)):
    return NewsStockCandidateService().generate(
        db, article_id, classification_limit, candidates_per_article, regenerate,
    )


@router.get("/stock-candidates")
def list_stock_candidates(article_id: int | None = None,
                          event_kind: str | None = None,
                          limit: int = Query(100, ge=1, le=500),
                          db: Session = Depends(get_db)):
    query = db.query(
        NewsStockCandidate, NewsClassification, NewsArticle, Security, Company,
    ).join(
        NewsClassification,
        NewsClassification.id == NewsStockCandidate.classification_id,
    ).join(
        NewsArticle, NewsArticle.id == NewsClassification.news_article_id,
    ).join(Security, Security.id == NewsStockCandidate.security_id).join(
        Company, Company.id == Security.company_id,
    )
    if article_id is not None:
        query = query.filter(NewsArticle.id == article_id)
    if event_kind:
        query = query.filter(NewsClassification.event_kind == event_kind.upper())
    rows = query.order_by(
        NewsArticle.published_at.desc(), NewsStockCandidate.rank,
    ).limit(limit).all()
    return {"count": len(rows), "data": [{
        "article_id": article.id, "title": article.title,
        "article_url": article.url,
        "published_at": article.published_at.isoformat(),
        "source": article.source,
        "event_kind": classification.event_kind,
        "classification_rationale": classification.rationale,
        "expected_direction": candidate.expected_direction,
        "rank": candidate.rank, "relevance_score": candidate.relevance_score,
        "confidence": candidate.confidence, "ticker": security.ticker,
        "market": security.market, "company_name": company.name,
        "industry_id": candidate.industry_id,
        "explanation": candidate.explanation, "version": candidate.version,
    } for candidate, classification, article, security, company in rows]}

@router.get("/impact-status")
def news_impact_status(db: Session = Depends(get_db)):
    validation = db.query(NewsCandidateValidationRun).order_by(
        NewsCandidateValidationRun.evaluated_at.desc(),
        NewsCandidateValidationRun.id.desc(),
    ).first()
    backtest = db.query(BacktestRun).filter(
        BacktestRun.score_version == "news-link-v1",
    ).order_by(BacktestRun.created_at.desc(), BacktestRun.id.desc()).first()
    report = backtest.report if backtest and backtest.report else {}
    metric = (report.get("metrics") or {}).get("1d") or {}
    acceptance = (report.get("horizon_acceptance") or {}).get("1d") or {}
    if backtest is None:
        status = "NOT_RUN"
    elif backtest.status == "PASSED":
        status = "VERIFIED"
    elif backtest.status == "FAILED":
        status = "REJECTED"
    else:
        status = "VALIDATING"
    return {
        "status": status,
        "candidate_quality_status": validation.status if validation else "NOT_RUN",
        "backtest_status": backtest.status if backtest else "NOT_RUN",
        "backtest_run_id": backtest.id if backtest else None,
        "updated_at": backtest.created_at.isoformat() if backtest else None,
        "one_day": {
            "observations": metric.get("observations", 0),
            "eligible_candidates": metric.get("eligible_candidates", 0),
            "pending_candidates": metric.get("pending_candidates", 0),
            "outcome_coverage_rate": metric.get("outcome_coverage_rate", 0.0),
            "minimum_outcome_coverage": (report.get("summary") or {}).get(
                "minimum_outcome_coverage", 0.8,
            ),
            "direction_hit_rate": metric.get("direction_hit_rate"),
            "average_market_excess": metric.get("average_market_excess"),
            "acceptance_status": acceptance.get("status", "NOT_RUN"),
            "reasons": acceptance.get("reasons", []),
        },
    }

@router.post("/stock-candidates/validate", dependencies=[Depends(require_admin)])
def validate_stock_candidates(as_of: date | None = None,
                              db: Session = Depends(get_db)):
    return NewsCandidateValidationService().validate(db, as_of)


@router.get("/stock-candidates/validation-runs/latest")
def latest_stock_candidate_validation(db: Session = Depends(get_db)):
    run = db.query(NewsCandidateValidationRun).order_by(
        NewsCandidateValidationRun.evaluated_at.desc(),
        NewsCandidateValidationRun.id.desc(),
    ).first()
    if run is None:
        return {"status": "NOT_RUN", "data": None}
    return {"status": run.status, "data": {
        "run_id": run.id, "version": run.version,
        "as_of": run.as_of.isoformat(), "report": run.report,
        "evaluated_at": run.evaluated_at.isoformat(),
    }}

@router.post("/prune-stale", dependencies=[Depends(require_admin)])
def prune_stale_news(max_age_days: int = Query(7, ge=1, le=365),
                     as_of: date | None = None,
                     dry_run: bool = True,
                     db: Session = Depends(get_db)):
    return NewsCollectionService.prune_stale(db, max_age_days, as_of, dry_run)