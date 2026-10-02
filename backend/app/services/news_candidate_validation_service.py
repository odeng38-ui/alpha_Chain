from collections import Counter
from datetime import date, datetime, time, timedelta

from sqlalchemy.orm import Session

from app.models.schema import (
    NewsArticle,
    NewsCandidateValidationRun,
    NewsClassification,
    NewsStockCandidate,
)


class NewsCandidateValidationService:
    version = "news-candidate-acceptance-v1"
    thresholds = {
        "max_article_age_days": 7,
        "min_fresh_article_rate": 0.80,
        "min_price_evidence_rate": 0.80,
        "min_article_coverage_rate": 1.00,
        "max_duplicate_rate": 0.00,
        "max_industry_concentration": 0.70,
        "min_candidates_per_article": 10,
    }

    def validate(self, db: Session, as_of: date | None = None):
        as_of = as_of or date.today()
        window_start = datetime.combine(
            as_of - timedelta(days=self.thresholds["max_article_age_days"]), time.min,
        )
        window_end = datetime.combine(as_of + timedelta(days=1), time.min)
        all_classification_count = db.query(NewsClassification).filter(
            NewsClassification.version == "news-rules-v1",
        ).count()
        classifications = db.query(NewsClassification, NewsArticle).join(
            NewsArticle, NewsArticle.id == NewsClassification.news_article_id,
        ).filter(
            NewsClassification.version == "news-rules-v1",
            NewsArticle.published_at >= window_start,
            NewsArticle.published_at < window_end,
        ).all()
        active_classification_ids = [item.id for item, _ in classifications]
        all_candidate_count = db.query(NewsStockCandidate).filter(
            NewsStockCandidate.version == "news-link-v1",
        ).count()
        candidates = db.query(NewsStockCandidate).filter(
            NewsStockCandidate.version == "news-link-v1",
            NewsStockCandidate.classification_id.in_(active_classification_ids),
        ).all() if active_classification_ids else []
        article_dates = {article.id: article.published_at.date()
                         for _, article in classifications}
        total_articles = len(article_dates)
        fresh_ids = {article_id for article_id, published in article_dates.items()
                     if 0 <= (as_of - published).days <= self.thresholds["max_article_age_days"]}
        linked_article_ids = {item.classification_id for item in candidates}
        classification_to_article = {classification.id: article.id
                                     for classification, article in classifications}
        linked_news_ids = {classification_to_article[item_id] for item_id in linked_article_ids
                           if item_id in classification_to_article}
        keys = [(item.classification_id, item.security_id) for item in candidates]
        unique_keys = set(keys)
        counts_by_classification = Counter(item.classification_id for item in candidates)
        industry_counts = Counter(item.industry_id for item in candidates)
        price_evidence = sum(
            (item.explanation or {}).get("data_quality") == "OK" for item in candidates
        )
        total_candidates = len(candidates)
        fresh_rate = len(fresh_ids) / total_articles if total_articles else 0.0
        evidence_rate = price_evidence / total_candidates if total_candidates else 0.0
        coverage_rate = len(linked_news_ids) / total_articles if total_articles else 0.0
        duplicate_rate = 1.0 - len(unique_keys) / total_candidates if total_candidates else 0.0
        concentration = max(industry_counts.values(), default=0) / total_candidates if total_candidates else 0.0
        minimum_candidates = min(counts_by_classification.values(), default=0)
        checks = {
            "fresh_articles": fresh_rate >= self.thresholds["min_fresh_article_rate"],
            "price_evidence": evidence_rate >= self.thresholds["min_price_evidence_rate"],
            "article_coverage": coverage_rate >= self.thresholds["min_article_coverage_rate"],
            "duplicates": duplicate_rate <= self.thresholds["max_duplicate_rate"],
            "industry_concentration": concentration <= self.thresholds["max_industry_concentration"],
            "candidate_depth": minimum_candidates >= self.thresholds["min_candidates_per_article"],
        }
        report = {
            "as_of": as_of.isoformat(), "thresholds": self.thresholds,
            "scope": {
                "window_start": window_start.isoformat(),
                "window_end_exclusive": window_end.isoformat(),
                "active_classifications": total_articles,
                "historical_classifications": all_classification_count,
                "active_candidates": total_candidates,
                "historical_candidates": all_candidate_count,
            },
            "metrics": {
                "articles": total_articles, "fresh_articles": len(fresh_ids),
                "fresh_article_rate": round(fresh_rate, 6),
                "stale_article_ids": sorted(set(article_dates) - fresh_ids),
                "candidates": total_candidates,
                "price_evidence_candidates": price_evidence,
                "price_evidence_rate": round(evidence_rate, 6),
                "article_coverage_rate": round(coverage_rate, 6),
                "duplicate_rate": round(duplicate_rate, 6),
                "industry_concentration": round(concentration, 6),
                "industry_counts": dict(sorted(industry_counts.items())),
                "minimum_candidates_per_article": minimum_candidates,
            },
            "checks": checks,
            "failed_checks": [name for name, passed in checks.items() if not passed],
        }
        status = "PASSED" if all(checks.values()) else "FAILED"
        run = NewsCandidateValidationRun(
            version=self.version, status=status, as_of=as_of,
            report=report, evaluated_at=datetime.utcnow(),
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        return {"run_id": run.id, "version": self.version,
                "status": status, "report": report}