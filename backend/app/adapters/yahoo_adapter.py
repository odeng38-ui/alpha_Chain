import hashlib
import json
from datetime import date, datetime, time, timedelta, timezone
from typing import List, Optional

import httpx

from app.adapters.base import AdapterError, BrokerAdapter, DataNotFoundError, OHLCVRecord


class YahooFinanceAdapter(BrokerAdapter):
    """Yahoo chart API fallback for Korean equities in serverless environments."""

    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout

    @property
    def name(self) -> str:
        return "yahoo-finance"

    def _symbols(self, ticker: str, market: str) -> list[str]:
        if market.upper() == "KOSDAQ":
            return [f"{ticker}.KQ"]
        if market.upper() == "KOSPI":
            return [f"{ticker}.KS"]
        return [f"{ticker}.KS", f"{ticker}.KQ"]

    def fetch_ohlcv(
        self,
        ticker: str,
        start_date: date,
        end_date: date,
        market: str = "KOSPI",
    ) -> List[OHLCVRecord]:
        period1 = int(datetime.combine(start_date, time.min, timezone.utc).timestamp())
        period2 = int(
            datetime.combine(end_date + timedelta(days=1), time.min, timezone.utc).timestamp()
        )
        for symbol in self._symbols(ticker, market):
            response = httpx.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                params={"period1": period1, "period2": period2, "interval": "1d"},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=self.timeout,
            )
            if response.status_code == 404:
                continue
            try:
                response.raise_for_status()
                result = response.json()["chart"]["result"][0]
            except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
                raise AdapterError(f"Yahoo Finance request failed for {symbol}") from exc
            timestamps = result.get("timestamp") or []
            if not timestamps:
                continue
            quote = (result.get("indicators", {}).get("quote") or [{}])[0]
            adjusted = (
                (result.get("indicators", {}).get("adjclose") or [{}])[0].get("adjclose")
                or []
            )
            offset = int(result.get("meta", {}).get("gmtoffset") or 32400)
            local_timezone = timezone(timedelta(seconds=offset))
            records = []
            for index, timestamp in enumerate(timestamps):
                close = self._value(quote.get("close"), index)
                if close is None:
                    continue
                raw = {
                    "symbol": symbol,
                    "timestamp": timestamp,
                    "quote": {
                        key: self._value(quote.get(key), index)
                        for key in ("open", "high", "low", "close", "volume")
                    },
                }
                records.append(
                    OHLCVRecord(
                        ticker=ticker,
                        trade_date=datetime.fromtimestamp(
                            timestamp, timezone.utc
                        ).astimezone(local_timezone).date(),
                        open=self._value(quote.get("open"), index),
                        high=self._value(quote.get("high"), index),
                        low=self._value(quote.get("low"), index),
                        close=close,
                        volume=self._integer(quote.get("volume"), index),
                        value=None,
                        adjusted_close=self._value(adjusted, index) or close,
                        raw_hash=hashlib.sha256(
                            json.dumps(raw, sort_keys=True).encode()
                        ).hexdigest(),
                    )
                )
            if records:
                return records
        raise DataNotFoundError(f"No price data found for ticker={ticker}")

    @staticmethod
    def _value(values, index: int) -> Optional[float]:
        if not values or index >= len(values) or values[index] is None:
            return None
        return float(values[index])

    @classmethod
    def _integer(cls, values, index: int) -> Optional[int]:
        value = cls._value(values, index)
        return int(value) if value is not None else None

    def fetch_latest(
        self,
        ticker: str,
        market: str = "KOSPI",
    ) -> Optional[OHLCVRecord]:
        records = self.fetch_ohlcv(
            ticker,
            date.today() - timedelta(days=10),
            date.today(),
            market,
        )
        return records[-1] if records else None

    def get_trading_days(
        self,
        start_date: date,
        end_date: date,
        market: str = "KOSPI",
    ) -> List[date]:
        current = start_date
        days = []
        while current <= end_date:
            if current.weekday() < 5:
                days.append(current)
            current += timedelta(days=1)
        return days
