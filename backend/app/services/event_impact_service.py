from collections import defaultdict
from datetime import date, timedelta

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
