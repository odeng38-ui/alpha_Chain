from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.adapters.gdelt_news_adapter import GdeltNewsAdapter
from app.db.session import get_db
from app.models.schema import NewsArticle
from app.security import require_admin
from app.services.news_collection_service import NewsCollectionService

router = APIRouter(prefix="/news", tags=["News"])


@router.post("/sync", dependencies=[Depends(require_admin)])
def sync_news(timespan: str = Query("24h", pattern=r"^[1-9][0-9]*(min|h|d|w)$"),
              max_records: int = Query(100, ge=1, le=250),
              db: Session = Depends(get_db)):
    return NewsCollectionService(GdeltNewsAdapter()).sync(db, timespan, max_records)


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