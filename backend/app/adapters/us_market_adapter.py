from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

import httpx

from app.adapters.base import AdapterError, DataNotFoundError


@dataclass(frozen=True)
class USMarketRecord:
    symbol: str
    trade_date: date
    close: float
    volume: Optional[int]


class YahooUSMarketAdapter:
    """Fetch daily US market data from Yahoo's chart endpoint."""

    def __init__(self, timeout: float = 20.0):
        self.timeout = timeout

    def fetch_daily(self, symbol: str, start_date: date, end_date: date) -> list[USMarketRecord]:
        period1 = int(datetime.combine(start_date, time.min, timezone.utc).timestamp())
        period2 = int(datetime.combine(end_date + timedelta(days=1), time.min, timezone.utc).timestamp())
        response = httpx.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={"period1": period1, "period2": period2, "interval": "1d"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=self.timeout,
        )
        try:
            response.raise_for_status()
            result = response.json()["chart"]["result"][0]
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise AdapterError(f"Yahoo US market request failed for {symbol}") from exc
        timestamps = result.get("timestamp") or []
        quote = (result.get("indicators", {}).get("quote") or [{}])[0]
        closes = quote.get("close") or []
        volumes = quote.get("volume") or []
        records = []
        for index, timestamp in enumerate(timestamps):
            if index >= len(closes) or closes[index] is None:
                continue
            volume = volumes[index] if index < len(volumes) else None
            records.append(USMarketRecord(
                symbol=symbol,
                trade_date=datetime.fromtimestamp(timestamp, timezone.utc).date(),
                close=float(closes[index]),
                volume=int(volume) if volume is not None else None,
            ))
        if not records:
            raise DataNotFoundError(f"No US market data found for symbol={symbol}")
        return records