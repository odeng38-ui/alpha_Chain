from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.schema import (
    Company,
    DailyPrice,
    NewsArticle,
    NewsClassification,
    NewsStockCandidate,
    Security,
)


class NewsStockCandidateService:
    version = "news-link-v1"

    @staticmethod
    def _turnover(price) -> float:
        if price.value is not None and float(price.value) > 0:
            return float(price.value)
        return float(price.adjusted_close or price.close or 0) * float(price.volume or 0)

    def generate(self, db: Session, article_id: int | None = None,
                 classification_limit: int = 100, candidates_per_article: int = 20,
                 regenerate: bool = False):
        query = db.query(NewsClassification, NewsArticle).join(
            NewsArticle, NewsArticle.id == NewsClassification.news_article_id
        ).filter(NewsClassification.version == "news-rules-v1")
        if article_id is not None:
            query = query.filter(NewsArticle.id == article_id)
        if not regenerate:
            query = query.filter(~NewsClassification.id.in_(
                db.query(NewsStockCandidate.classification_id).filter(
                    NewsStockCandidate.version == self.version
                )
            ))
        rows = query.order_by(NewsArticle.published_at.desc()).limit(classification_limit).all()
        created = updated = deleted = classifications_linked = 0
        for classification, article in rows:
            industries = list(classification.industries or [])
            if not industries:
                continue
            securities = db.query(Security).join(Company).filter(
                Security.security_type == "COMMON",
                Security.effective_to.is_(None),
                Company.status == "ACTIVE",
                Company.industry_id.in_(industries),
            ).all()
            ids = [security.id for security in securities]
            if not ids:
                continue
            cutoff = article.published_at.date()
            latest_dates = db.query(
                DailyPrice.security_id,
                func.max(DailyPrice.trade_date).label("trade_date"),
            ).filter(
                DailyPrice.security_id.in_(ids),
                DailyPrice.trade_date <= cutoff,
            ).group_by(DailyPrice.security_id).subquery()
            prices = db.query(DailyPrice).join(
                latest_dates,
                (DailyPrice.security_id == latest_dates.c.security_id)
                & (DailyPrice.trade_date == latest_dates.c.trade_date),
            ).all()
            price_by_security = {price.security_id: price for price in prices}
            turnovers = sorted(self._turnover(price) for price in prices)
            ranked = []
            for security in securities:
                price = price_by_security.get(security.id)
                industry_rank = industries.index(security.company.industry_id)
                industry_relevance = max(0.65, 1.0 - 0.10 * industry_rank)
                turnover = self._turnover(price) if price is not None else 0.0
                liquidity = (
                    sum(value <= turnover for value in turnovers) / len(turnovers)
                    if price is not None and turnovers else 0.0
                )
                age_days = max(0, (cutoff - price.trade_date).days) if price is not None else None
                freshness = max(0.0, 1.0 - age_days / 10.0) if age_days is not None else 0.0
                relevance = round(
                    100.0 * float(classification.confidence)
                    * (0.75 * industry_relevance + 0.15 * liquidity + 0.10 * freshness),
                    4,
                )
                ranked.append((relevance, security, industry_relevance,
                               liquidity, freshness, turnover, price))
            ranked.sort(key=lambda item: (-item[0], item[1].id))
            selected = ranked[:candidates_per_article]
            existing = {
                item.security_id: item for item in db.query(NewsStockCandidate).filter(
                    NewsStockCandidate.classification_id == classification.id,
                    NewsStockCandidate.version == self.version,
                    NewsStockCandidate.security_id.in_([item[1].id for item in selected]),
                ).all()
            } if selected else {}
            for rank, (score, security, exposure, liquidity, freshness,
                       turnover, price) in enumerate(selected, 1):
                candidate = existing.get(security.id)
                if candidate is None:
                    candidate = NewsStockCandidate(
                        classification_id=classification.id,
                        security_id=security.id,
                        version=self.version,
                    )
                    db.add(candidate)
                    created += 1
                else:
                    updated += 1
                candidate.industry_id = security.company.industry_id
                candidate.expected_direction = classification.direction
                candidate.relevance_score = score
                candidate.confidence = classification.confidence
                candidate.rank = rank
                candidate.explanation = {
                    "event_kind": classification.event_kind,
                    "matched_industry": security.company.industry_id,
                    "industry_relevance": round(exposure, 4),
                    "liquidity_percentile": round(liquidity, 6),
                    "turnover_proxy": round(turnover, 2),
                    "price_trade_date": price.trade_date.isoformat() if price is not None else None,
                    "price_freshness": round(freshness, 6),
                    "no_lookahead_cutoff": cutoff.isoformat(),
                    "data_quality": "OK" if price is not None else "MISSING_PRE_NEWS_PRICE",
                    "formula": "classification_confidence * industry * liquidity * freshness",
                }
            selected_ids = [item[1].id for item in selected]
            deleted += db.query(NewsStockCandidate).filter(
                NewsStockCandidate.classification_id == classification.id,
                NewsStockCandidate.version == self.version,
                ~NewsStockCandidate.security_id.in_(selected_ids),
            ).delete(synchronize_session=False) if selected_ids else 0
            classifications_linked += int(bool(selected))
        db.commit()
        return {
            "version": self.version, "processed": len(rows),
            "classifications_linked": classifications_linked,
            "created": created, "updated": updated, "deleted": deleted,
        }