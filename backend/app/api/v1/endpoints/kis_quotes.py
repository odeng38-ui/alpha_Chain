"""KIS On-Demand Quote and Historical Candles API Endpoints.

Protected by `require_admin` to prevent unauthorized or public access.
Provides on-demand domestic stock quotes and historical candle data.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.adapters.kis_quote_adapter import (
    KisAdapterError,
    KisAuthError,
    KisConfigError,
    KisCurrentQuote,
    KisDataNotFoundError,
    KisHistoricalQuotes,
    KisRateLimitError,
    KisServerError,
    KisTimeoutError,
)
from app.security import require_admin
from app.services.kis_quote_service import KisQuoteService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/kis/quotes", tags=["KIS On-Demand Quotes"])

_service_instance: Optional[KisQuoteService] = None


def get_kis_quote_service() -> KisQuoteService:
    global _service_instance
    if _service_instance is None:
        _service_instance = KisQuoteService()
    return _service_instance


def _map_kis_exception_to_http(exc: Exception) -> HTTPException:
    """Map KIS adapter / service exceptions to appropriate HTTP status codes without leaking secrets."""
    if isinstance(exc, ValueError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        )
    if isinstance(exc, KisConfigError):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="KIS integration is not configured on this server.",
        )
    if isinstance(exc, KisRateLimitError):
        return HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="KIS API rate limit exceeded. Please retry later.",
        )
    if isinstance(exc, KisTimeoutError):
        return HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="KIS API request timed out.",
        )
    if isinstance(exc, KisDataNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )
    if isinstance(exc, KisAuthError):
        return HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="KIS authentication failed with broker API.",
        )
    if isinstance(exc, KisServerError):
        return HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="KIS broker server returned an error.",
        )
    if isinstance(exc, KisAdapterError):
        return HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"KIS provider error: {exc}",
        )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Internal error processing KIS quote request.",
    )


@router.get(
    "/{ticker}",
    response_model=KisCurrentQuote,
    summary="Get current domestic stock quote via KIS Open API",
    dependencies=[Depends(require_admin)],
)
def get_current_quote(
    ticker: str,
    bypass_cache: bool = Query(False, description="Bypass short-lived in-memory cache"),
    service: KisQuoteService = Depends(get_kis_quote_service),
) -> KisCurrentQuote:
    """Retrieve on-demand real-time or snapshot quote for a Korean stock (e.g. 042700)."""
    try:
        return service.get_current_quote(ticker=ticker, bypass_cache=bypass_cache)
    except Exception as exc:
        raise _map_kis_exception_to_http(exc) from exc


@router.get(
    "/{ticker}/history",
    response_model=KisHistoricalQuotes,
    summary="Get historical OHLCV candles via KIS Open API",
    dependencies=[Depends(require_admin)],
)
def get_historical_quotes(
    ticker: str,
    period: str = Query("D", pattern="^(D|W|M|d|w|m)$", description="Candle period: D, W, or M"),
    start_date: Optional[date] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[date] = Query(None, description="End date (YYYY-MM-DD)"),
    adjust: bool = Query(True, description="Whether to request adjusted prices"),
    service: KisQuoteService = Depends(get_kis_quote_service),
) -> KisHistoricalQuotes:
    """Retrieve on-demand historical OHLCV candles (Day/Week/Month) for a Korean stock."""
    try:
        return service.get_historical_quotes(
            ticker=ticker,
            period=period,
            start_date=start_date,
            end_date=end_date,
            adjust=adjust,
        )
    except Exception as exc:
        raise _map_kis_exception_to_http(exc) from exc
