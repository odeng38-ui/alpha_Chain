import hashlib
import json
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from statistics import mean, median

from sqlalchemy.orm import Session

from app.models.schema import (
    BacktestRun,
    Company,
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
    def _benchmark(db: Session, entry_date: date, exit_date: date):
        entry_rows = db.query(DailyPrice).filter(
            DailyPrice.trade_date == entry_date,
        ).all()
        exit_rows = db.query(DailyPrice).filter(
            DailyPrice.trade_date == exit_date,
        ).all()
        entry_by_security = {row.security_id: float(row.open or 0) for row in entry_rows}
        exit_by_security = {
            row.security_id: float(row.adjusted_close or row.close or 0)
            for row in exit_rows
        }
        ids = set(entry_by_security) & set(exit_by_security)
        industries = dict(db.query(Security.id, Company.industry_id).join(
            Company, Company.id == Security.company_id,
        ).filter(Security.id.in_(ids)).all()) if ids else {}
        returns = {}
        by_industry = defaultdict(list)
        for security_id in ids:
            entry_price = entry_by_security[security_id]
            exit_price = exit_by_security[security_id]
            if entry_price <= 0 or exit_price <= 0:
                continue
            value = exit_price / entry_price - 1.0
            returns[security_id] = value
            industry = industries.get(security_id)
            if industry:
                by_industry[industry].append(value)
        return {
            "market_return": median(returns.values()) if returns else 0.0,
            "market_count": len(returns),
            "industry_returns": {
                industry: median(values) for industry, values in by_industry.items()
            },
            "industry_counts": {
                industry: len(values) for industry, values in by_industry.items()
            },
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

    def _diagnostics(self, samples, horizon_key):
        dimensions = {
            "rank_band": lambda sample: (
                "01-05" if sample["rank"] <= 5 else
                "06-10" if sample["rank"] <= 10 else "11+"
            ),
            "relevance_band": lambda sample: (
                "80+" if sample["relevance_score"] >= 80 else
                "70-79.99" if sample["relevance_score"] >= 70 else "below-70"
            ),
            "event_kind": lambda sample: sample["event_kind"],
            "industry": lambda sample: sample["industry_id"],
            "direction": lambda sample: sample["direction"],
        }
        report = {}
        for dimension, classifier in dimensions.items():
            groups = defaultdict(list)
            for sample in samples:
                outcome = sample["outcomes"].get(horizon_key)
                if outcome is not None:
                    groups[classifier(sample)].append(outcome)
            report[dimension] = {
                label: self._metrics(outcomes)
                for label, outcomes in sorted(groups.items())
            }
        return report

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
                "event_kind": classification.event_kind,
                "rank": candidate.rank,
                "relevance_score": candidate.relevance_score,
                "confidence": candidate.confidence,
                "published_at": article.published_at.isoformat(),
                "outcomes": {},
            }
            for horizon in horizons:
                sample["outcomes"][f"{horizon}d"] = self._outcome(
                    db, candidate, article, horizon,
                )
            samples.append(sample)
        metrics = {}
        diagnostics = {}
        horizon_acceptance = {}
        for horizon in horizons:
            key = f"{horizon}d"
            outcomes = []
            benchmark_cache = {}
            for sample in samples:
                outcome = sample["outcomes"][key]
                if outcome is None:
                    continue
                pair = (date.fromisoformat(outcome["entry_date"]),
                        date.fromisoformat(outcome["exit_date"]))
                if pair not in benchmark_cache:
                    benchmark_cache[pair] = self._benchmark(db, *pair)
                benchmark = benchmark_cache[pair]
                sign = 1.0 if sample["direction"] == "POSITIVE" else -1.0
                market = benchmark["market_return"]
                industry = benchmark["industry_returns"].get(
                    sample["industry_id"], market,
                )
                outcome["market_benchmark_return"] = round(market, 10)
                outcome["industry_benchmark_return"] = round(industry, 10)
                outcome["market_benchmark_count"] = benchmark["market_count"]
                outcome["industry_benchmark_count"] = benchmark["industry_counts"].get(
                    sample["industry_id"], 0,
                )
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
            diagnostics[key] = self._diagnostics(samples, key)
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
            "metrics": metrics, "diagnostics": diagnostics,
            "horizon_acceptance": horizon_acceptance,
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