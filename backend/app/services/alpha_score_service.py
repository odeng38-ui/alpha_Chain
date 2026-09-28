import hashlib
import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload

from app.models.schema import (
    AlphaWeightConfig,
    DailyPrice,
    DisclosureEvent,
    FeatureSnapshot,
    FinancialFact,
    MacroObservation,
    Relationship,
    ScoreSnapshot,
    Security,
)

COMPONENTS = ("Macro", "Industry", "Fundamental", "Momentum", "Disclosure", "Chain")
FORMULAS = {
    "Macro": "mean(unemployment_inverse, yield_curve, policy_rate_inverse)",
    "Industry": "percentile_rank(peer_20d_return)",
    "Fundamental": "mean(operating_margin, roa, debt_inverse)",
    "Momentum": "mean(return_20d, return_60d, price_vs_ma20)",
    "Disclosure": "50 + 10 * positive - 10 * negative, clipped",
    "Chain": "mean(verified_edge_confidence) with evidence count bonus",
}


def clamp(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 4)


def scale(value: float, low: float, high: float, inverse: bool = False) -> float:
    result = (value - low) / (high - low) * 100
    return clamp(100 - result if inverse else result)


class AlphaScoreService:
    config_path = Path(__file__).resolve().parents[1] / "config" / "alpha_score_v1.json"

    def load_config(self, db: Session, version: str = "v1.0") -> Dict:
        stored = db.get(AlphaWeightConfig, version)
        if stored:
            return {"version": version, "weights": stored.weights, "horizons": ["5d", "20d", "60d"]}
        config = json.loads(self.config_path.read_text(encoding="utf-8"))
        if version != config["version"]:
            raise ValueError("score config version not found")
        db.add(AlphaWeightConfig(
            version=version, weights=config["weights"], changed_by="system",
            reason="versioned file default",
        ))
        db.flush()
        return config

    @staticmethod
    def save_weights(db: Session, version: str, weights: Dict[str, float],
                     changed_by: str, reason: str) -> AlphaWeightConfig:
        if set(weights) != set(COMPONENTS) or any(value < 0 for value in weights.values()):
            raise ValueError("weights must contain six non-negative components")
        if sum(weights.values()) <= 0:
            raise ValueError("weight sum must be positive")
        if db.get(AlphaWeightConfig, version):
            raise ValueError("weight version already exists")
        normalized = {key: round(value / sum(weights.values()), 8) for key, value in weights.items()}
        row = AlphaWeightConfig(
            version=version, weights=normalized, changed_by=changed_by, reason=reason,
        )
        db.add(row)
        db.commit()
        return row

    @staticmethod
    def _returns(db: Session, security_id: int, as_of: date, count: int = 61):
        return db.query(DailyPrice).filter(
            DailyPrice.security_id == security_id, DailyPrice.trade_date <= as_of,
        ).order_by(DailyPrice.trade_date.desc()).limit(count).all()

    def _momentum(self, db: Session, security: Security, as_of: date) -> Tuple[Optional[float], Dict, Optional[datetime], Optional[str]]:
        rows = self._returns(db, security.id, as_of)
        if (
            len(rows) < 21
            or (rows[0].adjusted_close is None and rows[0].close is None)
            or (rows[20].adjusted_close is None and rows[20].close is None)
        ):
            return None, {"price_rows": len(rows)}, None, "at least 21 price observations required"
        closes = [float(row.adjusted_close or row.close) for row in rows]
        ret20 = closes[0] / closes[20] - 1
        ret60 = closes[0] / closes[60] - 1 if len(closes) >= 61 else None
        ma20 = sum(closes[:20]) / 20
        parts = [scale(ret20, -0.2, 0.2), scale(closes[0] / ma20 - 1, -0.1, 0.1)]
        if ret60 is not None:
            parts.append(scale(ret60, -0.35, 0.35))
        inputs = {"return_20d": ret20, "return_60d": ret60, "price_vs_ma20": closes[0] / ma20 - 1,
                  "latest_trade_date": rows[0].trade_date.isoformat()}
        return clamp(sum(parts) / len(parts)), inputs, datetime.combine(rows[0].trade_date, time.max), None

    def _industry(self, db: Session, security: Security, as_of: date) -> Tuple[Optional[float], Dict, Optional[datetime], Optional[str]]:
        if not security.company or not security.company.industry_id:
            return None, {}, None, "industry classification missing"
        peers = db.query(Security).join(Security.company).filter(
            Security.company.has(industry_id=security.company.industry_id),
            Security.security_type == "COMMON",
        ).all()
        returns = {}
        latest = None
        for peer in peers:
            rows = self._returns(db, peer.id, as_of, 21)
            if len(rows) >= 21 and rows[0].close and rows[20].close:
                returns[peer.id] = float(rows[0].close) / float(rows[20].close) - 1
                latest = max(latest, rows[0].trade_date) if latest else rows[0].trade_date
        if security.id not in returns or len(returns) < 2:
            return None, {"peer_count": len(returns)}, None, "at least two peers with prices required"
        ordered = sorted(returns.values())
        rank = sum(item <= returns[security.id] for item in ordered)
        score = 100 * (rank - 1) / (len(ordered) - 1)
        return clamp(score), {"peer_count": len(returns), "return_20d": returns[security.id]}, datetime.combine(latest, time.max), None

    @staticmethod
    def _macro(db: Session, as_of: date) -> Tuple[Optional[float], Dict, Optional[datetime], Optional[str]]:
        cutoff = datetime.combine(as_of, time.max)
        values, available = {}, []
        for series_id in ("UNRATE", "T10Y2Y", "FEDFUNDS"):
            row = db.query(MacroObservation).filter(
                MacroObservation.series_id == series_id,
                MacroObservation.available_at <= cutoff,
            ).order_by(MacroObservation.observation_date.desc(), MacroObservation.vintage_date.desc()).first()
            if row and row.value is not None:
                values[series_id] = float(row.value)
                available.append(row.available_at)
        if len(values) < 2:
            return None, values, max(available) if available else None, "at least two macro inputs required"
        parts = []
        if "UNRATE" in values:
            parts.append(scale(values["UNRATE"], 2, 10, True))
        if "T10Y2Y" in values:
            parts.append(scale(values["T10Y2Y"], -2, 3))
        if "FEDFUNDS" in values:
            parts.append(scale(values["FEDFUNDS"], 0, 8, True))
        return clamp(sum(parts) / len(parts)), values, max(available), None

    @staticmethod
    def _fundamental(db: Session, security: Security, as_of: date) -> Tuple[Optional[float], Dict, Optional[datetime], Optional[str]]:
        cutoff = datetime.combine(as_of, time.max)
        rows = db.query(FinancialFact).filter(
            FinancialFact.company_id == security.company_id,
            FinancialFact.filed_at <= cutoff,
            FinancialFact.consolidated.is_(True),
        ).order_by(FinancialFact.filed_at.desc()).all()
        latest = {}
        aliases = {
            "revenue": ("Revenue", "\ub9e4\ucd9c\uc561"),
            "operating_income": ("OperatingIncomeLoss", "\uc601\uc5c5\uc774\uc775"),
            "assets": ("Assets", "\uc790\uc0b0\ucd1d\uacc4"),
            "liabilities": ("Liabilities", "\ubd80\ucc44\ucd1d\uacc4"),
        }
        for row in rows:
            text = f"{row.account_id}|{row.account_name}"
            for key, names in aliases.items():
                if key not in latest and any(name in text for name in names) and row.value is not None:
                    latest[key] = (float(row.value), row.filed_at)
        inputs = {key: value for key, (value, _) in latest.items()}
        parts = []
        if latest.get("revenue") and latest["revenue"][0] != 0 and latest.get("operating_income"):
            parts.append(scale(latest["operating_income"][0] / latest["revenue"][0], -0.1, 0.25))
        if latest.get("assets") and latest["assets"][0] != 0:
            if latest.get("operating_income"):
                parts.append(scale(latest["operating_income"][0] / latest["assets"][0], -0.05, 0.15))
            if latest.get("liabilities"):
                parts.append(scale(latest["liabilities"][0] / latest["assets"][0], 0.2, 1.0, True))
        if not parts:
            return None, inputs, None, "required financial ratios unavailable"
        latest_at = max(value[1] for value in latest.values())
        return clamp(sum(parts) / len(parts)), inputs, latest_at, None

    @staticmethod
    def _disclosure(db: Session, security: Security, as_of: date) -> Tuple[Optional[float], Dict, Optional[datetime], Optional[str]]:
        cutoff = datetime.combine(as_of, time.max)
        start = cutoff - timedelta(days=180)
        rows = db.query(DisclosureEvent).filter(
            DisclosureEvent.company_id == security.company_id,
            DisclosureEvent.event_date <= cutoff, DisclosureEvent.event_date >= start,
        ).all()
        if not rows:
            return None, {"event_count": 0}, None, "no disclosure events in lookback window"
        positive = sum(row.sentiment == "POSITIVE" for row in rows)
        negative = sum(row.sentiment == "NEGATIVE" for row in rows)
        latest = max(row.event_date for row in rows if row.event_date)
        return clamp(50 + 10 * positive - 10 * negative), {
            "event_count": len(rows), "positive": positive, "negative": negative,
        }, latest, None

    @staticmethod
    def _chain(db: Session, security: Security, as_of: date) -> Tuple[Optional[float], Dict, Optional[datetime], Optional[str]]:
        cutoff = datetime.combine(as_of, time.max)
        rows = db.query(Relationship).options(selectinload(Relationship.evidences)).filter(
            Relationship.status == "verified",
            or_(Relationship.source_id == security.company_id, Relationship.target_id == security.company_id),
            or_(Relationship.valid_from.is_(None), Relationship.valid_from <= as_of),
            or_(Relationship.valid_to.is_(None), Relationship.valid_to >= as_of),
        ).all()
        usable = [(row, [ev for ev in row.evidences if ev.published_at <= cutoff]) for row in rows]
        usable = [(row, evidence) for row, evidence in usable if evidence]
        if not usable:
            return None, {"relationship_count": 0}, None, "no verified relationship evidence"
        confidences = [float(row.confidence) for row, _ in usable]
        evidence_count = sum(len(items) for _, items in usable)
        latest = max(ev.published_at for _, items in usable for ev in items)
        score = sum(confidences) / len(confidences) * 80 + min(evidence_count, 10) * 2
        return clamp(score), {"relationship_count": len(usable), "evidence_count": evidence_count,
                              "mean_confidence": sum(confidences) / len(confidences)}, latest, None

    def build_features(self, db: Session, security: Security, as_of: date, version: str) -> Dict[str, FeatureSnapshot]:
        calculators = {"Macro": self._macro, "Industry": self._industry, "Fundamental": self._fundamental,
                       "Momentum": self._momentum, "Disclosure": self._disclosure, "Chain": self._chain}
        result = {}
        for name, calculator in calculators.items():
            args = (db, as_of) if name == "Macro" else (db, security, as_of)
            value, inputs, available_at, missing = calculator(*args)
            row = db.query(FeatureSnapshot).filter_by(
                security_id=security.id, as_of_date=as_of,
                feature_name=name, feature_version=version,
            ).first() or FeatureSnapshot(
                security_id=security.id, as_of_date=as_of, feature_name=name, feature_version=version,
            )
            db.add(row)
            row.value = Decimal(str(value)) if value is not None else None
            row.formula, row.inputs = FORMULAS[name], inputs
            row.source_available_at, row.missing_reason = available_at, missing
            result[name] = row
        db.flush()
        return result

    def score(self, db: Session, security_id: int, as_of: date, horizon: str = "20d",
              version: str = "v1.0") -> ScoreSnapshot:
        security = db.get(Security, security_id)
        if not security:
            raise LookupError("security not found")
        config = self.load_config(db, version)
        if horizon not in config["horizons"]:
            raise ValueError("unsupported horizon")
        features = self.build_features(db, security, as_of, version)
        components = {name: float(row.value) if row.value is not None else None for name, row in features.items()}
        available = {name: value for name, value in components.items() if value is not None}
        if not available:
            raise ValueError("no features available for score")
        denominator = sum(config["weights"][name] for name in available)
        total = sum(value * config["weights"][name] for name, value in available.items()) / denominator
        completeness = len(available) / len(COMPONENTS)
        chain_quality = (features["Chain"].inputs or {}).get("mean_confidence", 0.0)
        confidence = round(0.8 * completeness + 0.2 * chain_quality, 6)
        factors = sorted(((name, value - 50) for name, value in available.items()), key=lambda item: item[1])
        explanations = {
            "positive_factors": [{"component": name, "impact": round(impact, 4)} for name, impact in reversed(factors) if impact > 0][:3],
            "risk_factors": [{"component": name, "impact": round(impact, 4)} for name, impact in factors if impact < 0][:3],
            "missing_components": [{"component": name, "reason": features[name].missing_reason}
                                   for name in COMPONENTS if components[name] is None],
        }
        config_hash = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
        row = db.query(ScoreSnapshot).filter_by(
            security_id=security_id, as_of_date=as_of, horizon=horizon, version=version,
        ).first() or ScoreSnapshot(
            security_id=security_id, as_of_date=as_of, horizon=horizon, version=version,
        )
        db.add(row)
        row.component, row.total, row.confidence = components, round(total, 6), confidence
        row.weights, row.explanations, row.completeness = config["weights"], explanations, completeness
        row.config_hash = config_hash
        db.commit()
        db.refresh(row)
        return row

    def batch(self, db: Session, as_of: date, security_ids: Optional[Iterable[int]] = None,
              horizon: str = "20d", version: str = "v1.0") -> Dict:
        query = db.query(Security).filter(Security.security_type == "COMMON")
        if security_ids:
            query = query.filter(Security.id.in_(security_ids))
        success, failed = 0, []
        for security in query.order_by(Security.id):
            try:
                self.score(db, security.id, as_of, horizon, version)
                success += 1
            except (ValueError, LookupError) as exc:
                db.rollback()
                failed.append({"security_id": security.id, "error": str(exc)})
        return {"success": success, "failed": failed}

