from collections import defaultdict
from datetime import date, timedelta
from statistics import median

from sqlalchemy.orm import Session

from app.models.schema import Company, DailyPrice, EventImpactCandidate, GlobalEvent, Security

SYMBOL_INDUSTRY_EXPOSURE = {
    "^SOX": {
        "SEMICONDUCTORS_ELECTRONICS": 1.00,
        "ELECTRICAL_EQUIPMENT": 0.75,
        "SOFTWARE_IT": 0.55,
        "MACHINERY": 0.35,
    },
    "^IXIC": {
        "SOFTWARE_IT": 1.00,
        "SEMICONDUCTORS_ELECTRONICS": 0.90,
        "TELECOMMUNICATIONS": 0.55,
        "MEDIA_CONTENT": 0.45,
        "PHARMA_BIOTECH": 0.35,
    },
    "^DJI": {
        "INDUSTRIALS": 1.00,
        "FINANCIALS": 0.80,
        "AUTOMOBILES": 0.65,
        "CONSUMER_GOODS": 0.60,
        "MACHINERY": 0.55,
    },
    "^GSPC": {
        "FINANCIALS": 0.75,
        "INDUSTRIALS": 0.70,
        "CONSUMER_GOODS": 0.65,
        "SOFTWARE_IT": 0.65,
        "SEMICONDUCTORS_ELECTRONICS": 0.65,
        "ENERGY_CHEMICALS": 0.55,
        "PHARMA_BIOTECH": 0.50,
    },
}


class EventImpactService:
    version = "impact-v1"

    @staticmethod
    def _latest_price(db: Session, security_id: int, as_of: date):
        return db.query(DailyPrice).filter(
            DailyPrice.security_id == security_id,
            DailyPrice.trade_date <= as_of,
        ).order_by(DailyPrice.trade_date.desc()).first()

    def generate(self, db: Session, event_id: int, limit: int = 100):
        event = db.get(GlobalEvent, event_id)
        if event is None:
            raise LookupError("global event not found")
        exposure_map = SYMBOL_INDUSTRY_EXPOSURE.get(event.symbol or "", {})
        if not exposure_map:
            raise ValueError("event symbol has no industry exposure map")
        as_of = event.available_at.date()
        securities = db.query(Security).join(Security.company).filter(
            Security.security_type == "COMMON",
            Security.effective_to.is_(None),
            Company.industry_id.in_(list(exposure_map)),
        ).all()
        ranked = []
        for security in securities:
            industry = security.company.industry_id
            exposure = exposure_map[industry]
            price = self._latest_price(db, security.id, as_of)
            if price is None:
                continue
            age_days = max(0, (as_of - price.trade_date).days)
            freshness = max(0.0, 1.0 - age_days / 10.0)
            confidence = round(0.7 * exposure + 0.3 * freshness, 6)
            magnitude = round(float(event.shock_score) * exposure * (0.8 + 0.2 * freshness), 4)
            signed_score = magnitude if event.direction == "POSITIVE" else -magnitude
            ranked.append((abs(signed_score), security, exposure, freshness, confidence, signed_score, price))
        ranked.sort(key=lambda item: (-item[0], item[1].id))
        selected = ranked[:limit]
        created = updated = 0
        for rank, (_, security, exposure, freshness, confidence, signed_score, price) in enumerate(selected, 1):
            row = db.query(EventImpactCandidate).filter_by(
                event_id=event.id, security_id=security.id, version=self.version,
            ).first()
            values = {
                "industry_id": security.company.industry_id,
                "exposure": exposure,
                "impact_score": signed_score,
                "confidence": confidence,
                "rank": rank,
                "explanation": {
                    "event_symbol": event.symbol,
                    "event_direction": event.direction,
                    "event_shock_score": event.shock_score,
                    "industry_exposure": exposure,
                    "price_trade_date": price.trade_date.isoformat(),
                    "price_freshness": round(freshness, 6),
                    "formula": "signed(shock_score * industry_exposure * (0.8 + 0.2 * price_freshness))",
                },
            }
            if row is None:
                row = EventImpactCandidate(event_id=event.id, security_id=security.id, version=self.version)
                db.add(row)
                created += 1
            else:
                updated += 1
            for key, value in values.items():
                setattr(row, key, value)
        db.commit()
        return {"event_id": event.id, "version": self.version, "eligible": len(ranked),
                "created": created, "updated": updated, "count": len(selected)}

class EventImpactV2Service(EventImpactService):
    version = "impact-v2"

    @staticmethod
    def _historical_sensitivity(prices, events):
        aligned_returns = []
        for event in events:
            reaction_index = next(
                (index for index, price in enumerate(prices)
                 if price.trade_date >= event.available_at.date()),
                None,
            )
            if reaction_index is None or reaction_index == 0:
                continue
            previous = prices[reaction_index - 1]
            reaction = prices[reaction_index]
            previous_close = float(previous.adjusted_close or previous.close or 0)
            reaction_close = float(reaction.adjusted_close or reaction.close or 0)
            if previous_close <= 0 or reaction_close <= 0:
                continue
            raw_return = reaction_close / previous_close - 1.0
            direction_sign = 1.0 if event.direction == "POSITIVE" else -1.0
            aligned_returns.append(raw_return * direction_sign)
        if not aligned_returns:
            return {"sample_count": 0, "aligned_mean_return": 0.0,
                    "direction_consistency": 0.5, "sensitivity_score": 50.0}
        mean_return = sum(aligned_returns) / len(aligned_returns)
        consistency = sum(value > 0 for value in aligned_returns) / len(aligned_returns)
        sensitivity_score = max(0.0, min(100.0, 50.0 + mean_return / 0.05 * 50.0))
        return {"sample_count": len(aligned_returns),
                "aligned_mean_return": round(mean_return, 8),
                "direction_consistency": round(consistency, 6),
                "sensitivity_score": round(sensitivity_score, 4)}

    def generate(self, db: Session, event_id: int, limit: int = 100):
        event = db.get(GlobalEvent, event_id)
        if event is None:
            raise LookupError("global event not found")
        exposure_map = SYMBOL_INDUSTRY_EXPOSURE.get(event.symbol or "", {})
        if not exposure_map:
            raise ValueError("event symbol has no industry exposure map")
        historical_events = db.query(GlobalEvent).filter(
            GlobalEvent.symbol == event.symbol,
            GlobalEvent.direction == event.direction,
            GlobalEvent.available_at < event.available_at,
        ).order_by(GlobalEvent.available_at).all()
        earliest = min(
            (row.available_at.date() for row in historical_events),
            default=event.available_at.date(),
        ) - timedelta(days=10)
        securities = db.query(Security).join(Security.company).filter(
            Security.security_type == "COMMON",
            Security.effective_to.is_(None),
            Company.industry_id.in_(list(exposure_map)),
        ).all()
        security_ids = [row.id for row in securities]
        price_rows = db.query(DailyPrice).filter(
            DailyPrice.security_id.in_(security_ids),
            DailyPrice.trade_date >= earliest,
            DailyPrice.trade_date < event.available_at.date(),
        ).order_by(DailyPrice.security_id, DailyPrice.trade_date).all() if security_ids else []
        prices_by_security = defaultdict(list)
        for price in price_rows:
            prices_by_security[price.security_id].append(price)

        ranked = []
        for security in securities:
            prices = prices_by_security[security.id]
            if not prices:
                continue
            latest = prices[-1]
            age_days = max(0, (event.available_at.date() - latest.trade_date).days)
            freshness = max(0.0, 1.0 - age_days / 10.0)
            exposure = exposure_map[security.company.industry_id]
            sensitivity = self._historical_sensitivity(prices, historical_events)
            reliability = min(1.0, sensitivity["sample_count"] / 5.0)
            confidence = round(
                0.35 * exposure + 0.20 * freshness + 0.25 * reliability
                + 0.20 * sensitivity["direction_consistency"], 6,
            )
            sensitivity_multiplier = 0.75 + 0.5 * sensitivity["sensitivity_score"] / 100.0
            magnitude = min(100.0, float(event.shock_score) * exposure
                            * (0.75 + 0.25 * freshness) * sensitivity_multiplier)
            signed_score = round(magnitude if event.direction == "POSITIVE" else -magnitude, 4)
            rank_score = abs(signed_score) * (0.7 + 0.3 * confidence)
            ranked.append((rank_score, security, exposure, freshness, confidence,
                           signed_score, latest, sensitivity))
        ranked.sort(key=lambda item: (-item[0], item[1].id))
        selected = ranked[:limit]
        existing = {
            row.security_id: row for row in db.query(EventImpactCandidate).filter(
                EventImpactCandidate.event_id == event.id,
                EventImpactCandidate.version == self.version,
                EventImpactCandidate.security_id.in_([item[1].id for item in selected]),
            ).all()
        } if selected else {}
        created = updated = 0
        for rank, (_, security, exposure, freshness, confidence, signed_score,
                   latest, sensitivity) in enumerate(selected, 1):
            row = existing.get(security.id)
            if row is None:
                row = EventImpactCandidate(
                    event_id=event.id, security_id=security.id, version=self.version,
                )
                db.add(row)
                created += 1
            else:
                updated += 1
            row.industry_id = security.company.industry_id
            row.exposure = exposure
            row.impact_score = signed_score
            row.confidence = confidence
            row.rank = rank
            row.explanation = {
                "event_symbol": event.symbol,
                "event_direction": event.direction,
                "event_shock_score": event.shock_score,
                "industry_exposure": exposure,
                "price_trade_date": latest.trade_date.isoformat(),
                "price_freshness": round(freshness, 6),
                "historical_sensitivity": sensitivity,
                "no_lookahead_cutoff": event.available_at.isoformat(),
                "formula": "signed(shock * exposure * freshness * historical_sensitivity)",
            }
        db.commit()
        return {"event_id": event.id, "version": self.version,
                "historical_event_count": len(historical_events),
                "eligible": len(ranked), "created": created,
                "updated": updated, "count": len(selected)}

class EventImpactV3Service(EventImpactV2Service):
    version = "impact-v3"

    def generate(self, db: Session, event_id: int, limit: int = 100):
        event = db.get(GlobalEvent, event_id)
        if event is None:
            raise LookupError("global event not found")
        v2_result = EventImpactV2Service().generate(db, event_id, 500)
        v2_rows = db.query(EventImpactCandidate).filter_by(
            event_id=event_id, version="impact-v2",
        ).all()
        if not v2_rows:
            return {"event_id": event_id, "version": self.version,
                    "eligible": 0, "created": 0, "updated": 0, "count": 0}
        market_median = median(
            row.explanation["historical_sensitivity"]["aligned_mean_return"]
            for row in v2_rows
        )
        security_ids = [row.security_id for row in v2_rows]
        price_rows = db.query(DailyPrice).filter(
            DailyPrice.security_id.in_(security_ids),
            DailyPrice.trade_date < event.available_at.date(),
        ).order_by(DailyPrice.security_id, DailyPrice.trade_date.desc()).all()
        latest = {}
        for price in price_rows:
            latest.setdefault(price.security_id, price)
        turnovers = {
            security_id: float((price.adjusted_close or price.close or 0) * (price.volume or 0))
            for security_id, price in latest.items()
        }
        ordered_turnovers = sorted(turnovers.values())

        ranked = []
        for row in v2_rows:
            sensitivity = row.explanation["historical_sensitivity"]
            abnormal = sensitivity["aligned_mean_return"] - market_median
            abnormal_score = max(0.0, min(100.0, 50.0 + abnormal / 0.05 * 50.0))
            turnover = turnovers.get(row.security_id, 0.0)
            liquidity_percentile = (
                sum(value <= turnover for value in ordered_turnovers) / len(ordered_turnovers)
                if ordered_turnovers else 0.0
            )
            reliability = min(1.0, sensitivity["sample_count"] / 8.0)
            confidence = round(
                0.30 * row.exposure + 0.25 * reliability
                + 0.25 * sensitivity["direction_consistency"]
                + 0.20 * liquidity_percentile, 6,
            )
            magnitude = min(
                100.0,
                float(event.shock_score) * row.exposure
                * (0.70 + 0.60 * abnormal_score / 100.0)
                * (0.90 + 0.10 * liquidity_percentile),
            )
            signed_score = round(
                magnitude if event.direction == "POSITIVE" else -magnitude, 4,
            )
            rank_score = abs(signed_score) * (0.65 + 0.35 * confidence)
            ranked.append((rank_score, row, signed_score, confidence,
                           abnormal, abnormal_score, turnover, liquidity_percentile))
        ranked.sort(key=lambda item: (-item[0], item[1].security_id))
        selected = ranked[:limit]
        existing = {
            row.security_id: row for row in db.query(EventImpactCandidate).filter(
                EventImpactCandidate.event_id == event_id,
                EventImpactCandidate.version == self.version,
                EventImpactCandidate.security_id.in_([item[1].security_id for item in selected]),
            ).all()
        } if selected else {}
        created = updated = 0
        shock_regime = "HIGH" if event.shock_score >= 80 else "MEDIUM" if event.shock_score >= 60 else "LOW"
        for rank, (_, source, score, confidence, abnormal, abnormal_score,
                   turnover, liquidity) in enumerate(selected, 1):
            row = existing.get(source.security_id)
            if row is None:
                row = EventImpactCandidate(event_id=event_id, security_id=source.security_id,
                                           version=self.version)
                db.add(row)
                created += 1
            else:
                updated += 1
            row.industry_id = source.industry_id
            row.exposure = source.exposure
            row.impact_score = score
            row.confidence = confidence
            row.rank = rank
            row.explanation = {
                **source.explanation,
                "market_adjustment": {
                    "universe_median_aligned_return": round(market_median, 8),
                    "abnormal_aligned_return": round(abnormal, 8),
                    "abnormal_score": round(abnormal_score, 4),
                },
                "liquidity": {"turnover_proxy": round(turnover, 2),
                              "percentile": round(liquidity, 6)},
                "shock_regime": shock_regime,
                "formula": "signed(shock * exposure * abnormal_response * liquidity)",
            }
        db.commit()
        return {"event_id": event_id, "version": self.version,
                "historical_event_count": v2_result["historical_event_count"],
                "eligible": len(ranked), "created": created,
                "updated": updated, "count": len(selected)}

class EventImpactV4Service(EventImpactService):
    calibration_version = "impact-v4-calibration-1"

    def __init__(self, horizon: int):
        if horizon not in {1, 5}:
            raise ValueError("impact-v4 horizon must be 1 or 5")
        self.horizon = horizon
        self.version = f"impact-v4-{horizon}d"

    def _confidence_calibration(self, event, sample_count: int):
        factors = {
            "history": 0.45 if sample_count == 0 else 0.70 if sample_count <= 3 else 1.0,
            "symbol": 0.85 if event.symbol == "^GSPC" else 1.0,
            "direction_horizon": (
                0.85 if self.horizon == 1 and event.direction == "POSITIVE" else
                0.75 if self.horizon == 5 and event.direction == "NEGATIVE" else 1.0
            ),
        }
        multiplier = 1.0
        for value in factors.values():
            multiplier *= value
        return factors, round(multiplier, 6)

    def generate(self, db: Session, event_id: int, limit: int = 100):
        event = db.get(GlobalEvent, event_id)
        if event is None:
            raise LookupError("global event not found")
        exposure_map = SYMBOL_INDUSTRY_EXPOSURE.get(event.symbol or "", {})
        if not exposure_map:
            raise ValueError("event symbol has no industry exposure map")
        historical = db.query(GlobalEvent).filter(
            GlobalEvent.symbol == event.symbol,
            GlobalEvent.direction == event.direction,
            GlobalEvent.available_at < event.available_at,
        ).order_by(GlobalEvent.available_at).all()
        earliest = min((item.available_at.date() for item in historical),
                       default=event.available_at.date()) - timedelta(days=10)
        securities = db.query(Security).join(Security.company).filter(
            Security.security_type == "COMMON", Security.effective_to.is_(None),
            Company.industry_id.in_(list(exposure_map)),
        ).all()
        ids = [item.id for item in securities]
        price_rows = db.query(DailyPrice).filter(
            DailyPrice.security_id.in_(ids), DailyPrice.trade_date >= earliest,
            DailyPrice.trade_date < event.available_at.date(),
        ).order_by(DailyPrice.security_id, DailyPrice.trade_date).all() if ids else []
        prices_by_security = defaultdict(list)
        for price in price_rows:
            prices_by_security[price.security_id].append(price)

        sensitivity_by_security = {}
        turnovers = {}
        for security in securities:
            prices = prices_by_security[security.id]
            if not prices:
                continue
            latest = prices[-1]
            turnovers[security.id] = float(
                (latest.adjusted_close or latest.close or 0) * (latest.volume or 0)
            )
            observations = []
            for prior_event in historical:
                entry_index = next((index for index, price in enumerate(prices)
                                    if price.trade_date >= prior_event.available_at.date()), None)
                exit_index = entry_index + self.horizon - 1 if entry_index is not None else None
                if entry_index is None or exit_index is None or exit_index >= len(prices):
                    continue
                entry = prices[entry_index]
                exit_row = prices[exit_index]
                entry_open = float(entry.open or 0)
                exit_close = float(exit_row.adjusted_close or exit_row.close or 0)
                if entry_open <= 0 or exit_close <= 0:
                    continue
                aligned = (exit_close / entry_open - 1.0) * (
                    1.0 if prior_event.direction == "POSITIVE" else -1.0
                )
                similarity = max(0.1, 1.0 - abs(
                    float(prior_event.shock_score) - float(event.shock_score)
                ) / 100.0)
                observations.append((aligned, similarity))
            if observations:
                weight_sum = sum(weight for _, weight in observations)
                weighted_mean = sum(value * weight for value, weight in observations) / weight_sum
                consistency = sum(value > 0 for value, _ in observations) / len(observations)
            else:
                weighted_mean, consistency = 0.0, 0.5
            sensitivity_by_security[security.id] = {
                "sample_count": len(observations),
                "weighted_open_to_close_return": round(weighted_mean, 8),
                "direction_consistency": round(consistency, 6),
            }
        market_median = median(
            item["weighted_open_to_close_return"]
            for item in sensitivity_by_security.values()
        ) if sensitivity_by_security else 0.0
        ordered_turnovers = sorted(turnovers.values())
        ranked = []
        for security in securities:
            sensitivity = sensitivity_by_security.get(security.id)
            prices = prices_by_security[security.id]
            if sensitivity is None or not prices:
                continue
            abnormal = sensitivity["weighted_open_to_close_return"] - market_median
            abnormal_score = max(0.0, min(100.0, 50.0 + abnormal / 0.05 * 50.0))
            turnover = turnovers.get(security.id, 0.0)
            liquidity = (sum(value <= turnover for value in ordered_turnovers)
                         / len(ordered_turnovers)) if ordered_turnovers else 0.0
            exposure = exposure_map[security.company.industry_id]
            reliability = min(1.0, sensitivity["sample_count"] / 8.0)
            base_confidence = (0.30 * exposure + 0.30 * reliability
                               + 0.25 * sensitivity["direction_consistency"]
                               + 0.15 * liquidity)
            calibration_factors, calibration_multiplier = self._confidence_calibration(
                event, sensitivity["sample_count"],
            )
            confidence = round(base_confidence * calibration_multiplier, 6)
            magnitude = min(100.0, float(event.shock_score) * exposure
                            * (0.70 + 0.60 * abnormal_score / 100.0)
                            * (0.90 + 0.10 * liquidity))
            score = round(magnitude if event.direction == "POSITIVE" else -magnitude, 4)
            ranked.append((abs(score) * (0.65 + 0.35 * confidence), security,
                           score, confidence, exposure, liquidity, turnover,
                           abnormal, abnormal_score, sensitivity, base_confidence,
                           calibration_factors, calibration_multiplier, prices[-1]))
        ranked.sort(key=lambda item: (-item[0], item[1].id))
        selected = ranked[:limit]
        selected_ids = [item[1].id for item in selected]
        stale_query = db.query(EventImpactCandidate).filter(
            EventImpactCandidate.event_id == event_id,
            EventImpactCandidate.version == self.version,
        )
        if selected_ids:
            stale_query = stale_query.filter(
                ~EventImpactCandidate.security_id.in_(selected_ids),
            )
        removed = stale_query.delete(synchronize_session=False)
        existing = {row.security_id: row for row in db.query(EventImpactCandidate).filter(
            EventImpactCandidate.event_id == event_id,
            EventImpactCandidate.version == self.version,
            EventImpactCandidate.security_id.in_(selected_ids),
        ).all()} if selected else {}
        created = updated = 0
        for rank, (_, security, score, confidence, exposure, liquidity, turnover,
                   abnormal, abnormal_score, sensitivity, base_confidence,
                   calibration_factors, calibration_multiplier, latest) in enumerate(selected, 1):
            row = existing.get(security.id)
            if row is None:
                row = EventImpactCandidate(event_id=event_id, security_id=security.id,
                                           version=self.version)
                db.add(row)
                created += 1
            else:
                updated += 1
            row.industry_id, row.exposure = security.company.industry_id, exposure
            row.impact_score, row.confidence, row.rank = score, confidence, rank
            row.explanation = {
                "event_symbol": event.symbol, "event_direction": event.direction,
                "event_shock_score": event.shock_score, "industry_exposure": exposure,
                "price_trade_date": latest.trade_date.isoformat(),
                "historical_sensitivity": sensitivity,
                "market_adjustment": {"universe_median_return": round(market_median, 8),
                                      "abnormal_return": round(abnormal, 8),
                                      "abnormal_score": round(abnormal_score, 4)},
                "liquidity": {"turnover_proxy": round(turnover, 2),
                              "percentile": round(liquidity, 6)},
                "confidence_calibration": {
                    "version": self.calibration_version,
                    "base_confidence": round(base_confidence, 6),
                    "factors": calibration_factors,
                    "multiplier": calibration_multiplier,
                },
                "training_horizon": f"{self.horizon}d",
                "entry_basis": "reaction_session_open",
                "no_lookahead_cutoff": event.available_at.isoformat(),
                "formula": "signed(shock * exposure * open_to_horizon_abnormal * liquidity)",
            }
        db.commit()
        return {"event_id": event_id, "version": self.version,
                "historical_event_count": len(historical), "eligible": len(ranked),
                "created": created, "updated": updated, "removed": removed,
                "count": len(selected)}
