import hashlib
import json
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from statistics import mean, median

from sqlalchemy.orm import Session

from app.models.schema import (
    BacktestRun,
    DailyPrice,
    NewsArticle,
    NewsCandidateValidationRun,
    NewsClassification,
    NewsStockCandidate,
    Security,
)


class NewsCandidateBacktestService:
    version = "news-link-v1"
    minimum_observations = 30
    transaction_cost = 0.003

    @staticmethod
    def _entry_date(published_at: datetime) -> date:
        korea_time = published_at + timedelta(hours=9)
        if korea_time.time() >= time(9, 0):
            return korea_time.date() + timedelta(days=1)
        return korea_time.date()

    def _outcome(self, db: Session, candidate, article, horizon: int):
        prices = db.query(DailyPrice).filter(
            DailyPrice.security_id == candidate.security_id,
            DailyPrice.trade_date >= self._entry_date(article.published_at),
        ).order_by(DailyPrice.trade_date).limit(horizon).all()
        if len(prices) < horizon:
            return None
        entry, exit_row = prices[0], prices[horizon - 1]
        entry_price = float(entry.open or 0)
        exit_price = float(exit_row.adjusted_close or exit_row.close or 0)
        if entry_price <= 0 or exit_price <= 0:
            return None
        raw_return = exit_price / entry_price - 1.0
        sign = 1.0 if candidate.expected_direction == "POSITIVE" else -1.0
        return {
            "entry_date": entry.trade_date.isoformat(),
            "entry_price": entry_price,
            "exit_date": exit_row.trade_date.isoformat(),
            "exit_price": exit_price,
            "raw_return": raw_return,
            "aligned_net_return": raw_return * sign - self.transaction_cost,
        }

    @staticmethod
    def _metrics(rows):
        if not rows:
            return {
                "observations": 0, "direction_hit_rate": None,
                "average_raw_return": None, "average_aligned_net_return": None,
                "average_market_excess": None, "average_industry_excess": None,
            }
        return {
            "observations": len(rows),
            "direction_hit_rate": round(mean(row["aligned_net_return"] > 0 for row in rows), 6),
            "average_raw_return": round(mean(row["raw_return"] for row in rows), 8),
            "average_aligned_net_return": round(mean(row["aligned_net_return"] for row in rows), 8),
            "average_market_excess": round(mean(row["aligned_market_excess"] for row in rows), 8),
            "average_industry_excess": round(mean(row["aligned_industry_excess"] for row in rows), 8),
        }

    def run(self, db: Session, name: str, horizons=(1, 5, 20),
            candidate_limit: int = 500) -> BacktestRun:
        validation = db.query(NewsCandidateValidationRun).order_by(
            NewsCandidateValidationRun.evaluated_at.desc(),
            NewsCandidateValidationRun.id.desc(),
        ).first()
        if validation is None or validation.status != "PASSED":
            raise ValueError("latest news candidate validation must be PASSED")
        rows = db.query(
            NewsStockCandidate, NewsClassification, NewsArticle, Security,
        ).join(
            NewsClassification,
            NewsClassification.id == NewsStockCandidate.classification_id,
        ).join(
            NewsArticle, NewsArticle.id == NewsClassification.news_article_id,
        ).join(Security, Security.id == NewsStockCandidate.security_id).filter(
            NewsStockCandidate.version == self.version,
            NewsStockCandidate.expected_direction.in_(("POSITIVE", "NEGATIVE")),
        ).order_by(NewsArticle.published_at, NewsStockCandidate.rank).limit(candidate_limit).all()
        samples = []
        for candidate, classification, article, security in rows:
            sample = {
                "candidate_id": candidate.id, "article_id": article.id,
                "security_id": security.id, "ticker": security.ticker,
                "industry_id": candidate.industry_id,
                "direction": candidate.expected_direction,
                "published_at": article.published_at.isoformat(),
                "outcomes": {},
            }
            for horizon in horizons:
                sample["outcomes"][f"{horizon}d"] = self._outcome(
                    db, candidate, article, horizon,
                )
            samples.append(sample)
        metrics = {}
        horizon_acceptance = {}
        for horizon in horizons:
            key = f"{horizon}d"
            outcomes = []
            by_article = defaultdict(list)
            by_article_industry = defaultdict(list)
            for sample in samples:
                outcome = sample["outcomes"][key]
                if outcome is None:
                    continue
                by_article[sample["article_id"]].append(outcome["raw_return"])
                by_article_industry[(sample["article_id"], sample["industry_id"])].append(
                    outcome["raw_return"]
                )
            for sample in samples:
                outcome = sample["outcomes"][key]
                if outcome is None:
                    continue
                sign = 1.0 if sample["direction"] == "POSITIVE" else -1.0
                market = median(by_article[sample["article_id"]])
                industry = median(by_article_industry[(sample["article_id"], sample["industry_id"])])
                outcome["aligned_market_excess"] = (
                    outcome["raw_return"] - market
                ) * sign - self.transaction_cost
                outcome["aligned_industry_excess"] = (
                    outcome["raw_return"] - industry
                ) * sign - self.transaction_cost
                for field in ("raw_return", "aligned_net_return", "aligned_market_excess",
                              "aligned_industry_excess"):
                    outcome[field] = round(outcome[field], 10)
                outcomes.append(outcome)
            metrics[key] = self._metrics(outcomes)
            if len(outcomes) < self.minimum_observations:
                status = "INSUFFICIENT_SAMPLE"
                reasons = [f"OBSERVATIONS_BELOW_{self.minimum_observations}"]
            else:
                reasons = []
                if metrics[key]["direction_hit_rate"] < 0.55:
                    reasons.append("DIRECTION_HIT_RATE_BELOW_55")
                if metrics[key]["average_market_excess"] <= 0:
                    reasons.append("NON_POSITIVE_MARKET_EXCESS")
                status = "PASSED" if not reasons else "FAILED"
            horizon_acceptance[key] = {"status": status, "reasons": reasons}
        statuses = {item["status"] for item in horizon_acceptance.values()}
        overall = ("FAILED" if "FAILED" in statuses else "INSUFFICIENT_SAMPLE"
                   if "INSUFFICIENT_SAMPLE" in statuses else "PASSED")
        frozen = {
            "version": self.version, "horizons": list(horizons),
            "candidate_ids": [sample["candidate_id"] for sample in samples],
            "samples": samples,
        }
        dataset_hash = hashlib.sha256(
            json.dumps(frozen, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        report = {
            "summary": {
                "directional_candidates": len(samples),
                "transaction_cost": self.transaction_cost,
                "minimum_observations": self.minimum_observations,
            },
            "metrics": metrics, "horizon_acceptance": horizon_acceptance,
            "samples": samples,
            "bias_checklist": {
                "candidate_acceptance_passed": True,
                "korea_time_conversion": True,
                "next_session_open_entry": True,
                "future_prices_only_for_outcome": True,
                "transaction_cost_applied": True,
            },
        }
        run = BacktestRun(
            name=name, score_version=self.version,
            horizon=",".join(f"{item}d" for item in horizons),
            config={"horizons": list(horizons), "candidate_limit": candidate_limit},
            dataset_hash=dataset_hash, parameter_adjustments=0,
            status=overall, report=report, completed_at=datetime.utcnow(),
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        return run