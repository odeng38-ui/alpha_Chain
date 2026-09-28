import hashlib
import json
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from statistics import mean
from typing import Dict, Iterable, List, Optional

from sqlalchemy.orm import Session

from app.models.schema import BacktestRun, DailyPrice, ScoreSnapshot, Security


def max_drawdown(returns: Iterable[float]) -> float:
    wealth = peak = 1.0
    drawdown = 0.0
    for value in returns:
        wealth *= 1 + value
        peak = max(peak, wealth)
        drawdown = min(drawdown, wealth / peak - 1)
    return round(drawdown, 8)


class BacktestService:
    config_path = Path(__file__).resolve().parents[1] / "config" / "backtest_v1.json"

    def load_config(self) -> Dict:
        return json.loads(self.config_path.read_text(encoding="utf-8"))

    @staticmethod
    def _eligible(security: Security, as_of: date) -> bool:
        return (
            (security.listed_at is None or security.listed_at <= as_of)
            and (security.delisted_at is None or security.delisted_at >= as_of)
            and (security.effective_from is None or security.effective_from <= as_of)
            and (security.effective_to is None or security.effective_to >= as_of)
        )

    @staticmethod
    def _price_value(row: DailyPrice, field: str = "close") -> Optional[float]:
        value = getattr(row, field) or row.adjusted_close or row.close
        return float(value) if value is not None else None

    def _sample(self, db: Session, score: ScoreSnapshot, horizon_days: int, config: Dict) -> Dict:
        security = db.get(Security, score.security_id)
        base = {
            "security_id": score.security_id, "company_id": security.company_id,
            "as_of_date": score.as_of_date.isoformat(), "score": score.total,
            "confidence": score.confidence, "market": security.market,
            "industry_id": security.company.industry_id if security.company else None,
            "delisted_at": security.delisted_at.isoformat() if security.delisted_at else None,
        }
        if not self._eligible(security, score.as_of_date):
            return {**base, "filled": False, "reason": "OUTSIDE_HISTORICAL_UNIVERSE"}
        prior = db.query(DailyPrice).filter(
            DailyPrice.security_id == security.id,
            DailyPrice.trade_date <= score.as_of_date,
        ).order_by(DailyPrice.trade_date.desc()).first()
        future = db.query(DailyPrice).filter(
            DailyPrice.security_id == security.id,
            DailyPrice.trade_date > score.as_of_date,
        ).order_by(DailyPrice.trade_date).limit(horizon_days + 1).all()
        if prior is None or not future:
            return {**base, "filled": False, "reason": "NO_ENTRY_QUOTE"}
        entry = future[0]
        if (entry.trade_date - score.as_of_date).days > config.get("max_entry_delay_days", 7):
            return {**base, "filled": False, "reason": "SUSPENDED_OR_NO_QUOTE"}
        if len(future) <= horizon_days:
            return {**base, "filled": False, "reason": "NO_EXIT_QUOTE"}
        prior_close = self._price_value(prior)
        entry_price = self._price_value(entry, "open")
        exit_row = future[horizon_days]
        exit_price = self._price_value(exit_row)
        if not prior_close or not entry_price or not exit_price:
            return {**base, "filled": False, "reason": "INVALID_PRICE"}
        gap = entry_price / prior_close - 1
        if gap >= config["limit_up_threshold"]:
            return {**base, "filled": False, "reason": "LIMIT_UP_ENTRY"}
        gross = exit_price / entry_price - 1
        round_trip_cost = 2 * (config["commission_bps"] + config["slippage_bps"]) / 10000
        return {
            **base, "filled": True, "reason": None,
            "entry_date": entry.trade_date.isoformat(), "entry_price": entry_price,
            "exit_date": exit_row.trade_date.isoformat(), "exit_price": exit_price,
            "gross_return": round(gross, 10), "net_return": round(gross - round_trip_cost, 10),
            "transaction_cost": round(round_trip_cost, 10),
            "score_config_hash": score.config_hash,
        }

    @staticmethod
    def _assign_splits(dates: List[str], config: Dict) -> Dict[str, str]:
        unique = sorted(set(dates))
        count = len(unique)
        train_end = max(1, int(count * config["train_ratio"]))
        validation_end = max(train_end, int(count * (config["train_ratio"] + config["validation_ratio"])))
        if count >= 3:
            train_end = min(train_end, count - 2)
            validation_end = min(max(validation_end, train_end + 1), count - 1)
        return {
            item: "train" if index < train_end else "validation" if index < validation_end else "test"
            for index, item in enumerate(unique)
        }

    @staticmethod
    def _assign_quantiles(samples: List[Dict], quantiles: int) -> None:
        by_date = defaultdict(list)
        for sample in samples:
            if sample["filled"]:
                by_date[sample["as_of_date"]].append(sample)
        for rows in by_date.values():
            rows.sort(key=lambda item: (-item["score"], item["security_id"]))
            for rank, row in enumerate(rows):
                row["quantile"] = min(quantiles, rank * quantiles // len(rows) + 1)

    @staticmethod
    def _add_benchmarks(samples: List[Dict]) -> None:
        market = defaultdict(list)
        industry = defaultdict(list)
        for row in samples:
            if not row["filled"]:
                continue
            market[(row["as_of_date"], row["market"])].append(row["gross_return"])
            if row["industry_id"]:
                industry[(row["as_of_date"], row["industry_id"])].append(row["gross_return"])
        for row in samples:
            if not row["filled"]:
                continue
            row["market_benchmark_return"] = mean(market[(row["as_of_date"], row["market"])])
            key = (row["as_of_date"], row["industry_id"])
            row["industry_benchmark_return"] = mean(industry[key]) if row["industry_id"] and industry[key] else None

    @staticmethod
    def _metrics(rows: List[Dict]) -> Dict:
        if not rows:
            return {"observations": 0, "average_gross_return": None, "average_net_return": None,
                    "win_rate": None, "max_drawdown": None, "turnover": None,
                    "market_excess_return": None, "industry_excess_return": None}
        by_date, holdings = defaultdict(list), {}
        for row in rows:
            by_date[row["as_of_date"]].append(row["net_return"])
            holdings.setdefault(row["as_of_date"], set()).add(row["security_id"])
        cohort_returns = [mean(by_date[item]) for item in sorted(by_date)]
        turnovers, previous = [], None
        for item in sorted(holdings):
            current = holdings[item]
            if previous is not None:
                turnovers.append(1 - len(previous & current) / max(len(previous), len(current), 1))
            previous = current
        industry_rows = [row for row in rows if row["industry_benchmark_return"] is not None]
        return {
            "observations": len(rows),
            "average_gross_return": round(mean(row["gross_return"] for row in rows), 8),
            "average_net_return": round(mean(row["net_return"] for row in rows), 8),
            "win_rate": round(mean(row["net_return"] > 0 for row in rows), 8),
            "max_drawdown": max_drawdown(cohort_returns),
            "turnover": round(mean(turnovers), 8) if turnovers else 0.0,
            "market_excess_return": round(mean(row["net_return"] - row["market_benchmark_return"] for row in rows), 8),
            "industry_excess_return": round(mean(row["net_return"] - row["industry_benchmark_return"] for row in industry_rows), 8) if industry_rows else None,
        }

    def run(
        self, db: Session, name: str, score_version: str = "v1.0", horizon: str = "20d",
        start: Optional[date] = None, end: Optional[date] = None,
        config_override: Optional[Dict] = None,
    ) -> BacktestRun:
        config = self.load_config()
        if config_override:
            config.update(config_override)
        if config["commission_bps"] <= 0:
            raise ValueError("commission_bps must be positive")
        try:
            horizon_days = int(horizon.removesuffix("d"))
        except ValueError as exc:
            raise ValueError("horizon must be formatted like 20d") from exc
        query = db.query(ScoreSnapshot).filter(
            ScoreSnapshot.version == score_version, ScoreSnapshot.horizon == horizon,
        )
        if start:
            query = query.filter(ScoreSnapshot.as_of_date >= start)
        if end:
            query = query.filter(ScoreSnapshot.as_of_date <= end)
        scores = query.order_by(ScoreSnapshot.as_of_date, ScoreSnapshot.security_id).all()
        samples = [self._sample(db, score, horizon_days, config) for score in scores]
        split_map = self._assign_splits([row["as_of_date"] for row in samples], config)
        for row in samples:
            row["split"] = split_map.get(row["as_of_date"], "train")
        self._assign_quantiles(samples, config["quantiles"])
        self._add_benchmarks(samples)
        filled = [row for row in samples if row["filled"]]
        metrics = {}
        for split in ("train", "validation", "test"):
            metrics[split] = {
                f"Q{quantile}": self._metrics([
                    row for row in filled if row["split"] == split and row["quantile"] == quantile
                ]) for quantile in range(1, config["quantiles"] + 1)
            }
        frozen = {"config": config, "samples": samples, "score_version": score_version, "horizon": horizon}
        dataset_hash = hashlib.sha256(json.dumps(frozen, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        unfilled_rate = (len(samples) - len(filled)) / len(samples) if samples else 1.0
        test_count = sum(row["split"] == "test" for row in filled)
        unfilled_by_reason = {
            reason: sum(not row["filled"] and row["reason"] == reason for row in samples)
            for reason in sorted({row["reason"] for row in samples if not row["filled"]})
        }
        failure_conditions = []
        if test_count < config["failure_conditions"]["minimum_test_observations"]:
            failure_conditions.append("INSUFFICIENT_HOLDOUT_OBSERVATIONS")
        if unfilled_rate > config["failure_conditions"]["maximum_unfilled_rate"]:
            failure_conditions.append("EXCESSIVE_UNFILLED_RATE")
        date_sets = {key: sorted({row["as_of_date"] for row in samples if row["split"] == key})
                     for key in ("train", "validation", "test")}
        report = {
            "summary": {"score_rows": len(samples), "filled_rows": len(filled),
                        "unfilled_rows": len(samples) - len(filled), "unfilled_rate": round(unfilled_rate, 8),
                        "unfilled_by_reason": unfilled_by_reason},
            "periods": {key: {"start": values[0] if values else None, "end": values[-1] if values else None}
                        for key, values in date_sets.items()},
            "metrics": metrics,
            "final_holdout": metrics["test"],
            "parameter_tuning": {"adjustments": config["parameter_adjustments"],
                                 "used_holdout": False},
            "benchmark_method": "historical scored-universe equal-weight by market and industry",
            "execution_assumptions": {key: config[key] for key in
                                      ("commission_bps", "slippage_bps", "limit_up_threshold")},
            "failure_conditions": failure_conditions,
            "bias_checklist": {
                "point_in_time_scores": True, "next_session_entry": True,
                "historical_listing_window": True, "delisted_securities_included": True,
                "positive_transaction_cost": config["commission_bps"] > 0,
                "suspension_and_limit_up_handled": True, "holdout_separated": True,
            },
        }
        report["dataset_manifest"] = {
            "hash": dataset_hash, "score_version": score_version, "horizon": horizon,
            "rows": len(samples), "config_version": config["version"],
        }
        run = BacktestRun(
            name=name, score_version=score_version, horizon=horizon, config=config,
            dataset_hash=dataset_hash, parameter_adjustments=config["parameter_adjustments"],
            train_start=date.fromisoformat(report["periods"]["train"]["start"]) if report["periods"]["train"]["start"] else None,
            train_end=date.fromisoformat(report["periods"]["train"]["end"]) if report["periods"]["train"]["end"] else None,
            validation_start=date.fromisoformat(report["periods"]["validation"]["start"]) if report["periods"]["validation"]["start"] else None,
            validation_end=date.fromisoformat(report["periods"]["validation"]["end"]) if report["periods"]["validation"]["end"] else None,
            test_start=date.fromisoformat(report["periods"]["test"]["start"]) if report["periods"]["test"]["start"] else None,
            test_end=date.fromisoformat(report["periods"]["test"]["end"]) if report["periods"]["test"]["end"] else None,
            status="FAILED_ACCEPTANCE" if failure_conditions else "COMPLETED",
            report=report, completed_at=datetime.utcnow(),
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        return run
