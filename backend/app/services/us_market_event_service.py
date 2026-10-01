import hashlib
import json
import statistics
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.schema import GlobalEvent

DEFAULT_US_SYMBOLS = ("^GSPC", "^IXIC", "^DJI", "^SOX")
SYMBOL_NAMES = {
    "^GSPC": "S&P 500",
    "^IXIC": "NASDAQ Composite",
    "^DJI": "Dow Jones Industrial Average",
    "^SOX": "PHLX Semiconductor Index",
}


class USMarketEventService:
    def __init__(self, adapter):
        self.adapter = adapter

    @staticmethod
    def detect(symbol, records, min_return=0.02, min_zscore=2.5, window=20):
        events = []
        returns = []
        for index in range(1, len(records)):
            daily_return = records[index].close / records[index - 1].close - 1.0
            history = returns[max(0, len(returns) - window):]
            zscore = None
            if len(history) >= window:
                deviation = statistics.stdev(history)
                if deviation > 0:
                    zscore = (daily_return - statistics.mean(history)) / deviation
            returns.append(daily_return)
            if abs(daily_return) < min_return and (zscore is None or abs(zscore) < min_zscore):
                continue
            return_strength = abs(daily_return) / min_return
            z_strength = abs(zscore) / min_zscore if zscore is not None else 0.0
            shock_score = min(100.0, 50.0 * max(return_strength, z_strength))
            events.append({
                "symbol": symbol,
                "trade_date": records[index].trade_date,
                "return_1d": daily_return,
                "zscore_20d": zscore,
                "shock_score": shock_score,
                "direction": "POSITIVE" if daily_return > 0 else "NEGATIVE",
                "close": records[index].close,
                "volume": records[index].volume,
            })
        return events

    def sync(self, db: Session, as_of: date | None = None, lookback_days: int = 180,
             symbols=DEFAULT_US_SYMBOLS):
        as_of = as_of or date.today()
        start_date = as_of - timedelta(days=max(60, min(lookback_days, 730)))
        created = updated = 0
        failures = []
        for symbol in symbols:
            try:
                records = self.adapter.fetch_daily(symbol, start_date, as_of)
                for detected in self.detect(symbol, records):
                    event_date = detected["trade_date"]
                    external_id = f"yahoo:{symbol}:{event_date.isoformat()}:price_shock"
                    payload = {
                        "close": detected["close"], "volume": detected["volume"],
                        "return_1d": detected["return_1d"], "zscore_20d": detected["zscore_20d"],
                    }
                    raw_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
                    row = db.query(GlobalEvent).filter(GlobalEvent.external_id == external_id).first()
                    values = dict(
                        source="YAHOO_FINANCE", origin_country="US", event_kind="MARKET_SHOCK",
                        symbol=symbol, title=f"{SYMBOL_NAMES.get(symbol, symbol)} {detected['direction'].lower()} shock",
                        summary=f"1-day return {detected['return_1d']:.2%}",
                        direction=detected["direction"], occurred_at=datetime.combine(event_date, datetime.min.time()),
                        available_at=datetime.combine(event_date + timedelta(days=1), datetime.min.time()),
                        return_1d=detected["return_1d"], zscore_20d=detected["zscore_20d"],
                        shock_score=detected["shock_score"], event_metadata=payload, raw_hash=raw_hash,
                    )
                    if row is None:
                        db.add(GlobalEvent(external_id=external_id, **values))
                        created += 1
                    elif row.raw_hash != raw_hash:
                        for key, value in values.items():
                            setattr(row, key, value)
                        updated += 1
            except Exception as exc:
                failures.append({"symbol": symbol, "error": str(exc)})
        db.commit()
        return {"as_of": as_of.isoformat(), "symbols": len(tuple(symbols)), "created": created,
                "updated": updated, "failures": failures}