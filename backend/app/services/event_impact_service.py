from datetime import date

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