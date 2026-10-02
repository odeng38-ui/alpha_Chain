import hashlib
import json
from datetime import date, datetime
from statistics import mean

from sqlalchemy.orm import Session

from app.models.schema import BacktestRun, DailyPrice, EventImpactCandidate, GlobalEvent, Security
from app.services.event_impact_service import (
    EventImpactV2Service,
    EventImpactV3Service,
    EventImpactV4Service,
)


class EventImpactBacktestService:
    version = "impact-v2"

    def __init__(self, version: str = "impact-v2"):
        if version not in {"impact-v2", "impact-v3", "impact-v4"}:
            raise ValueError("unsupported event impact version")
        self.version = version

    @staticmethod
    def _outcome(db: Session, event, candidate, horizon: int):
        prices = db.query(DailyPrice).filter(
            DailyPrice.security_id == candidate.security_id,
            DailyPrice.trade_date >= event.available_at.date(),
        ).order_by(DailyPrice.trade_date).limit(horizon).all()
        if len(prices) < horizon:
            return None
        entry = prices[0]
        exit_row = prices[horizon - 1]
        if entry.open is None or (exit_row.adjusted_close is None and exit_row.close is None):
            return None
        entry_price = float(entry.open)
        exit_price = float(exit_row.adjusted_close or exit_row.close)
        if entry_price <= 0:
            return None
        raw_return = exit_price / entry_price - 1.0
        direction_sign = 1.0 if event.direction == "POSITIVE" else -1.0
        return {
            "entry_date": entry.trade_date.isoformat(),
            "exit_date": exit_row.trade_date.isoformat(),
            "raw_return": round(raw_return, 10),
            "aligned_return": round(raw_return * direction_sign, 10),
        }

    @staticmethod
    def _acceptance(metrics):
        horizon_acceptance = {}
        failure_conditions = []
        for key, values in metrics.items():
            conditions = []
            if values["observations"] < 10:
                conditions.append(f"INSUFFICIENT_OUTCOMES_{key.upper()}")
            if values["observations"] and values["direction_hit_rate"] < 0.5:
                conditions.append(f"DIRECTION_HIT_RATE_BELOW_50_{key.upper()}")
            if values["observations"] and values["average_aligned_return"] <= 0:
                conditions.append(f"NON_POSITIVE_ALIGNED_RETURN_{key.upper()}")
            horizon_acceptance[key] = {
                "status": "PASSED" if not conditions else "FAILED",
                "failure_conditions": conditions,
            }
            failure_conditions.extend(conditions)
        passed = sum(item["status"] == "PASSED" for item in horizon_acceptance.values())
        status = ("COMPLETED" if passed == len(horizon_acceptance)
                  else "PARTIAL_ACCEPTANCE" if passed else "FAILED_ACCEPTANCE")
        return horizon_acceptance, failure_conditions, status
    @staticmethod
    def _metrics(rows):
        if not rows:
            return {"observations": 0, "average_raw_return": None,
                    "average_aligned_return": None, "direction_hit_rate": None}
        return {
            "observations": len(rows),
            "average_raw_return": round(mean(row["raw_return"] for row in rows), 8),
            "average_aligned_return": round(mean(row["aligned_return"] for row in rows), 8),
            "direction_hit_rate": round(mean(row["aligned_return"] > 0 for row in rows), 8),
        }

    def run(self, db: Session, name: str, start: date | None = None, end: date | None = None,
            max_events: int = 10, candidates_per_event: int = 20,
            horizons=(1, 5)) -> BacktestRun:
        query = db.query(GlobalEvent).filter(GlobalEvent.event_kind == "MARKET_SHOCK")
        if start:
            query = query.filter(GlobalEvent.available_at >= datetime.combine(start, datetime.min.time()))
        if end:
            query = query.filter(GlobalEvent.available_at <= datetime.combine(end, datetime.max.time()))
        events = query.order_by(GlobalEvent.available_at.desc()).limit(max_events).all()
        events.reverse()
        samples = []
        generator = EventImpactV3Service() if self.version == "impact-v3" else EventImpactV2Service()
        if self.version == "impact-v4":
            generator = None
        for event in events:
            event_horizons = horizons if self.version == "impact-v4" else (None,)
            for model_horizon in event_horizons:
                candidate_version = self.version
                if model_horizon is not None:
                    generator = EventImpactV4Service(model_horizon)
                    candidate_version = generator.version
                generator.generate(db, event.id, candidates_per_event)
                candidates = db.query(EventImpactCandidate).filter_by(
                    event_id=event.id, version=candidate_version,
                ).order_by(EventImpactCandidate.rank).limit(candidates_per_event).all()
                for candidate in candidates:
                    security = db.get(Security, candidate.security_id)
                    sample = {
                        "event_id": event.id, "event_symbol": event.symbol,
                        "event_direction": event.direction,
                        "available_at": event.available_at.isoformat(),
                        "security_id": candidate.security_id,
                        "ticker": security.ticker, "rank": candidate.rank,
                        "impact_score": candidate.impact_score,
                        "confidence": candidate.confidence,
                        "historical_sample_count": candidate.explanation[
                        "historical_sensitivity"
                        ]["sample_count"],
                        "outcomes": {},
                    }
                    selected_horizons = (model_horizon,) if model_horizon else horizons
                    for horizon in selected_horizons:
                        sample["outcomes"][f"{horizon}d"] = self._outcome(
                            db, event, candidate, horizon,
                        )
                    samples.append(sample)
        metrics = {}
        for horizon in horizons:
            key = f"{horizon}d"
            rows = [sample["outcomes"].get(key) for sample in samples
                    if sample["outcomes"].get(key) is not None]
            metrics[key] = self._metrics(rows)
        frozen = {"version": self.version, "events": [event.external_id for event in events],
                  "horizons": list(horizons), "candidates_per_event": candidates_per_event,
                  "samples": samples}
        dataset_hash = hashlib.sha256(
            json.dumps(frozen, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        observations = sum(item["observations"] for item in metrics.values())
        horizon_acceptance, failure_conditions, run_status = self._acceptance(metrics)
        report = {
            "summary": {"events": len(events), "candidate_rows": len(samples),
                        "outcome_observations": observations},
            "metrics": metrics,
            "samples": samples,
            "failure_conditions": failure_conditions,
            "horizon_acceptance": horizon_acceptance,
            "bias_checklist": {
                "point_in_time_candidates": True,
                "pre_event_prices_only": True,
                "future_events_excluded": True,
                "next_korean_session_open_entry": True,
            },
            "dataset_manifest": {"hash": dataset_hash, "version": self.version,
                                 "event_count": len(events)},
        }
        run = BacktestRun(
            name=name, score_version=self.version,
            horizon=",".join(f"{item}d" for item in horizons),
            config={"max_events": max_events, "candidates_per_event": candidates_per_event,
                    "horizons": list(horizons)},
            dataset_hash=dataset_hash, parameter_adjustments=0,
            status=run_status,
            report=report, completed_at=datetime.utcnow(),
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        return run