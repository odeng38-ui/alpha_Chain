from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.schema import NewsArticle, NewsClassification, NewsStockCandidate


class NewsCollectionService:
    def __init__(self, adapter):
        self.adapter = adapter

    def sync(self, db: Session, timespan: str = "24h", max_records: int = 100):
        records = self.adapter.fetch(timespan=timespan, max_records=max_records)
        ids = [item.external_id for item in records]
        existing = {
            row.external_id: row for row in db.query(NewsArticle).filter(
                NewsArticle.external_id.in_(ids)
            ).all()
        } if ids else {}
        created = updated = 0
        for item in records:
            row = existing.get(item.external_id)
            if row is None:
                row = NewsArticle(external_id=item.external_id, source=item.source)
                db.add(row)
                existing[item.external_id] = row
                created += 1
            elif row.raw_hash != item.raw_hash:
                updated += 1
            else:
                continue
            row.title = item.title
            row.url = item.url
            row.domain = item.domain
            row.language = item.language
            row.source_country = item.source_country
            row.published_at = item.published_at
            row.image_url = item.image_url
            row.raw_hash = item.raw_hash
            row.raw_metadata = item.raw_metadata
        db.commit()
        return {"sources": sorted({item.source for item in records}), "fetched": len(records),
                "created": created, "updated": updated,
                "unchanged": len(records) - created - updated}
    @staticmethod
    def prune_stale(db: Session, max_age_days: int = 7,
                    as_of: date | None = None, dry_run: bool = True):
        as_of = as_of or date.today()
        cutoff = datetime.combine(as_of - timedelta(days=max_age_days), datetime.min.time())
        article_ids = [row[0] for row in db.query(NewsArticle.id).filter(
            NewsArticle.published_at < cutoff,
        ).all()]
        classification_ids = [row[0] for row in db.query(NewsClassification.id).filter(
            NewsClassification.news_article_id.in_(article_ids),
        ).all()] if article_ids else []
        candidate_count = db.query(NewsStockCandidate).filter(
            NewsStockCandidate.classification_id.in_(classification_ids),
        ).count() if classification_ids else 0
        if article_ids and not dry_run:
            db.query(NewsArticle).filter(NewsArticle.id.in_(article_ids)).delete(
                synchronize_session=False,
            )
            db.commit()
        return {
            "as_of": as_of.isoformat(), "max_age_days": max_age_days,
            "cutoff": cutoff.isoformat(), "dry_run": dry_run,
            "articles": len(article_ids), "article_ids": sorted(article_ids),
            "classifications": len(classification_ids), "candidates": candidate_count,
        }