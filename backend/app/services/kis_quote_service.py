"""Korea Investment & Securities (KIS) quote service.

Provides high-level on-demand Korean market quote retrieval, validation,
and short-lived in-memory caching.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from app.adapters.kis_quote_adapter import (
    KisCurrentQuote,
    KisDataNotFoundError,
    KisHistoricalQuotes,
    KisQuoteAdapter,
)

logger = logging.getLogger(__name__)

# Max lookback window allowed for historical queries (5 years = 1826 days)
MAX_LOOKBACK_DAYS = 1826
QUOTE_CACHE_TTL_SECONDS = 10.0


class KisQuoteService:
    """Service orchestrating KIS stock quotes and candle queries."""

    def __init__(self, adapter: Optional[KisQuoteAdapter] = None) -> None:
        self.adapter = adapter or KisQuoteAdapter()
        self._quote_cache: dict[str, tuple[float, KisCurrentQuote]] = {}
        self._cache_lock = threading.Lock()

    def _validate_ticker(self, ticker: str) -> str:
        cleaned = (ticker or "").strip()
        if not re.match(r"^\d{6}$", cleaned):
            raise ValueError(f"Ticker must be a 6-digit numeric code, got: '{ticker}'")
        return cleaned

    def get_current_quote(self, ticker: str, bypass_cache: bool = False) -> KisCurrentQuote:
        """Fetch current quote with short-lived in-memory cache to prevent burst duplicates."""
        ticker = self._validate_ticker(ticker)

        if not bypass_cache:
            with self._cache_lock:
                cached = self._quote_cache.get(ticker)
                if cached:
                    ts, quote = cached
                    if (time.time() - ts) < QUOTE_CACHE_TTL_SECONDS:
                        return quote

        quote = self.adapter.get_current_quote(ticker)

        with self._cache_lock:
            self._quote_cache[ticker] = (time.time(), quote)

        return quote

    def get_historical_quotes(
        self,
        ticker: str,
        period: str = "D",
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        adjust: bool = True,
    ) -> KisHistoricalQuotes:
        """Fetch historical candles with date validation and boundary constraints."""
        ticker = self._validate_ticker(ticker)
        period = (period or "D").upper().strip()
        if period not in ("D", "W", "M"):
            raise ValueError(f"Invalid period '{period}'. Must be 'D' (day), 'W' (week), or 'M' (month).")

        today = datetime.now(timezone.utc).date()
        if end_date is None:
            end_date = today
        if start_date is None:
            if period == "D":
                start_date = end_date - timedelta(days=30)
            elif period == "W":
                start_date = end_date - timedelta(days=180)
            else:
                start_date = end_date - timedelta(days=365)

        if start_date > end_date:
            raise ValueError(
                f"start_date ({start_date}) cannot be later than end_date ({end_date})."
            )

        span_days = (end_date - start_date).days
        if span_days > MAX_LOOKBACK_DAYS:
            raise ValueError(
                f"Requested span ({span_days} days) exceeds maximum allowed lookback of {MAX_LOOKBACK_DAYS} days (5 years)."
            )

        try:
            return self.adapter.get_historical_candles(
                ticker=ticker,
                period=period,
                start_date=start_date,
                end_date=end_date,
                adjust=adjust,
            )
        except KisDataNotFoundError:
            fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            return KisHistoricalQuotes(
                ticker=ticker,
                source="KIS",
                period=period,
                adjustment="adjusted" if adjust else "unadjusted",
                currency="KRW",
                start_date=start_date.isoformat(),
                end_date=end_date.isoformat(),
                count=0,
                candles=[],
                fetched_at=fetched_at,
                status="no_data",
            )
