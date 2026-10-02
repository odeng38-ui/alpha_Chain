from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.adapters.gdelt_news_adapter import GdeltNewsAdapter
from app.adapters.google_news_adapter import FallbackNewsAdapter, GoogleNewsRssAdapter
from app.db.session import get_db
from app.models.schema import (
    Company,
    NewsArticle,
    NewsClassification,
    NewsStockCandidate,
    Security,
)
from app.security import require_admin
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
        NewsArticle, NewsArticle.id == NewsClassification.news_article_id
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
        "event_kind": classification.event_kind,
        "expected_direction": candidate.expected_direction,
        "rank": candidate.rank, "relevance_score": candidate.relevance_score,
        "confidence": candidate.confidence, "ticker": security.ticker,
        "market": security.market, "company_name": company.name,
        "industry_id": candidate.industry_id,
        "explanation": candidate.explanation, "version": candidate.version,
    } for candidate, classification, article, security, company in rows]}