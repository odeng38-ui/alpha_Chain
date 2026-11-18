"""Korea Investment & Securities (KIS) Open API on-demand quote adapter.

Strict compliance with ALPHA CHAIN Event-First + On-Demand market data principles:
- On-demand, read-only price quote and historical candle retrieval for specific tickers
- Token caching and proactive refresh
- Strict credential protection (no secrets in logs or responses)
- Rate limiting and retry handling
"""
from __future__ import annotations

import logging
import re
import threading
import time
from datetime import date, datetime, timezone
from typing import Any, Optional

import httpx
from pydantic import BaseModel, Field

from app.adapters.base import AdapterError, RateLimitError
from app.config import settings

logger = logging.getLogger(__name__)


class KisAdapterError(AdapterError):
    """Base exception for KIS adapter errors."""

    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class KisConfigError(KisAdapterError):
    """Missing or invalid KIS configuration."""

    pass


class KisAuthError(KisAdapterError):
    """Authentication or token issuance failure."""

    pass


class KisRateLimitError(RateLimitError, KisAdapterError):
    """API rate limit exceeded (HTTP 429)."""

    pass


class KisTimeoutError(KisAdapterError):
    """Request timed out (HTTP 504)."""

    pass


class KisDataNotFoundError(KisAdapterError):
    """Requested ticker or candle data not found."""

    pass


class KisServerError(KisAdapterError):
    """KIS server internal error or gateway issue."""

    pass


class KisCurrentQuote(BaseModel):
    """Standardized real-time or snapshot current quote model."""

    ticker: str
    source: str = "KIS"
    market: str = "KOSPI"
    currency: str = "KRW"
    price: float | int
    change: float | int
    change_rate_pct: float
    volume: int
    market_timestamp: Optional[str] = None
    fetched_at: str
    status: str = "success"


class KisCandle(BaseModel):
    """Standardized OHLCV candle model."""

    date: str  # YYYY-MM-DD
    open: float | int
    high: float | int
    low: float | int
    close: float | int
    volume: int
    value: Optional[float | int] = None


class KisHistoricalQuotes(BaseModel):
    """Standardized historical OHLCV candles response."""

    ticker: str
    source: str = "KIS"
    period: str  # D, W, M
    adjustment: str  # adjusted or unadjusted
    currency: str = "KRW"
    start_date: str  # YYYY-MM-DD
    end_date: str  # YYYY-MM-DD
    count: int
    candles: list[KisCandle] = Field(default_factory=list)
    fetched_at: str
    status: str = "success"


class KisQuoteAdapter:
    """Adapter for Korea Investment & Securities (KIS) Open API quote endpoints."""

    def __init__(
        self,
        app_key: Optional[str] = None,
        app_secret: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        client: Optional[httpx.Client] = None,
    ) -> None:
        self.app_key = (app_key if app_key is not None else settings.KIS_APP_KEY).strip()
        self.app_secret = (
            app_secret if app_secret is not None else settings.KIS_APP_SECRET
        ).strip()
        self.base_url = (
            base_url if base_url is not None else settings.effective_kis_base_url
        ).rstrip("/")
        self.timeout_seconds = (
            timeout_seconds if timeout_seconds is not None else settings.KIS_TIMEOUT_SECONDS
        )
        self._custom_client = client
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0
        self._lock = threading.Lock()

    def _get_client(self) -> httpx.Client:
        if self._custom_client is not None:
            return self._custom_client
        return httpx.Client(timeout=self.timeout_seconds)

    def _validate_credentials(self) -> None:
        if not self.app_key or not self.app_secret:
            raise KisConfigError(
                "KIS integration is not configured. KIS_APP_KEY and KIS_APP_SECRET must be set."
            )

    def get_token(self, force_refresh: bool = False) -> str:
        """Fetch or reuse KIS OAuth 2.0 access token."""
        with self._lock:
            now = time.time()
            if not force_refresh and self._access_token and now < (self._token_expires_at - 60):
                return self._access_token

            self._validate_credentials()
            url = f"{self.base_url}/oauth2/tokenP"
            payload = {
                "grant_type": "client_credentials",
                "appkey": self.app_key,
                "appsecret": self.app_secret,
            }
            headers = {"content-type": "application/json"}

            client = self._get_client()
            try:
                # Do not close injected client, but close on-demand client
                if self._custom_client is not None:
                    res = client.post(url, json=payload, headers=headers)
                else:
                    with client:
                        res = client.post(url, json=payload, headers=headers)
            except httpx.TimeoutException as exc:
                raise KisTimeoutError("KIS OAuth token request timed out", status_code=504) from exc
            except httpx.RequestError as exc:
                raise KisServerError(
                    f"KIS OAuth token connection failed: {type(exc).__name__}", status_code=502
                ) from exc

            if res.status_code in (401, 403):
                raise KisAuthError(
                    "KIS OAuth authentication failed. Please check your credentials.",
                    status_code=res.status_code,
                )
            if res.status_code == 429:
                raise KisRateLimitError(
                    "KIS OAuth token rate limit exceeded.", status_code=429
                )
            if res.status_code >= 500:
                raise KisServerError(
                    f"KIS OAuth server error: HTTP {res.status_code}", status_code=res.status_code
                )

            try:
                data = res.json()
            except Exception as exc:
                raise KisServerError("Failed to parse KIS token response as JSON", status_code=502) from exc

            access_token = data.get("access_token")
            if not access_token:
                error_desc = data.get("error_description", data.get("msg1", "No access token returned"))
                raise KisAuthError(f"KIS token issuance failed: {error_desc}")

            expires_in = float(data.get("expires_in", 86400))
            self._access_token = access_token
            self._token_expires_at = time.time() + expires_in
            return self._access_token

    def _execute_request_with_retry(
        self,
        endpoint: str,
        tr_id: str,
        params: dict[str, Any],
        max_retries: int = 2,
    ) -> dict[str, Any]:
        """Execute a KIS GET request with token auth, rate-limit backoff, and 1 token refresh retry."""
        token = self.get_token()
        url = f"{self.base_url}{endpoint}"
        retries = 0

        while True:
            headers = {
                "content-type": "application/json; charset=utf-8",
                "authorization": f"Bearer {token}",
                "appkey": self.app_key,
                "appsecret": self.app_secret,
                "tr_id": tr_id,
            }

            client = self._get_client()
            try:
                if self._custom_client is not None:
                    res = client.get(url, headers=headers, params=params)
                else:
                    with client:
                        res = client.get(url, headers=headers, params=params)
            except httpx.TimeoutException as exc:
                raise KisTimeoutError(f"KIS request to {endpoint} timed out", status_code=504) from exc
            except httpx.RequestError as exc:
                raise KisServerError(
                    f"KIS connection error to {endpoint}: {type(exc).__name__}", status_code=502
                ) from exc

            # Check for expired token response
            if res.status_code in (401, 403) and retries == 0:
                retries += 1
                token = self.get_token(force_refresh=True)
                continue

            if res.status_code == 429:
                if retries < max_retries:
                    retries += 1
                    time.sleep(0.5 * (2 ** retries))
                    continue
                raise KisRateLimitError("KIS API rate limit exceeded", status_code=429)

            if res.status_code in (401, 403):
                raise KisAuthError("KIS API authentication rejected", status_code=res.status_code)

            if res.status_code >= 500:
                raise KisServerError(
                    f"KIS server error: HTTP {res.status_code}", status_code=res.status_code
                )

            try:
                data = res.json()
            except Exception as exc:
                raise KisServerError(
                    f"Invalid JSON returned from KIS: {exc}", status_code=502
                ) from exc

            rt_cd = data.get("rt_cd")
            # Some KIS errors return HTTP 200 with non-zero rt_cd
            if rt_cd is not None and rt_cd != "0":
                msg_cd = str(data.get("msg_cd", ""))
                msg1 = str(data.get("msg1", ""))

                # Check token expiration inside business payload
                if ("EGW00123" in msg_cd or "token" in msg1.lower() or "인증" in msg1) and retries == 0:
                    retries += 1
                    token = self.get_token(force_refresh=True)
                    continue

                if "EGW00201" in msg_cd or "초과" in msg1 or "LIMIT" in msg_cd:
                    if retries < max_retries:
                        retries += 1
                        time.sleep(0.5 * (2 ** retries))
                        continue
                    raise KisRateLimitError(f"KIS rate limit: {msg1}", status_code=429)

                if "존재하지" in msg1 or "없습니다" in msg1 or "조회" in msg1 and "없음" in msg1:
                    raise KisDataNotFoundError(msg1)

                raise KisAdapterError(f"KIS error [{msg_cd}]: {msg1}")

            return data

    def get_current_quote(self, ticker: str) -> KisCurrentQuote:
        """Fetch current snapshot quote for a Korean stock."""
        ticker = ticker.strip()
        if not re.match(r"^\d{6}$", ticker):
            raise ValueError(f"Ticker must be a 6-digit numeric code, got: {ticker}")

        data = self._execute_request_with_retry(
            endpoint="/uapi/domestic-stock/v1/quotations/inquire-price",
            tr_id="FHKST01010100",
            params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": ticker,
            },
        )

        output = data.get("output")
        if not output or not output.get("stck_prpr"):
            raise KisDataNotFoundError(f"No price data found for ticker {ticker}")

        try:
            price = int(float(output["stck_prpr"]))
            change = int(float(output.get("prdy_vrss", 0)))
            change_rate_pct = round(float(output.get("prdy_ctrt", 0.0)), 2)
            volume = int(float(output.get("acml_vol", 0)))
        except (ValueError, TypeError) as exc:
            raise KisAdapterError(f"Failed to parse numeric quote data: {exc}") from exc

        kor_market = str(output.get("rprs_mrkt_kor_name", ""))
        if "코스닥" in kor_market or "KOSDAQ" in kor_market:
            market = "KOSDAQ"
        else:
            market = "KOSPI"

        # Determine market_timestamp
        bsop_date = str(output.get("stck_bsop_date") or "").strip()
        cntg_hour = str(output.get("stck_cntg_hour") or "").strip()
        market_timestamp: Optional[str] = None
        if len(bsop_date) == 8 and len(cntg_hour) == 6:
            try:
                market_timestamp = (
                    f"{bsop_date[:4]}-{bsop_date[4:6]}-{bsop_date[6:8]}"
                    f"T{cntg_hour[:2]}:{cntg_hour[2:4]}:{cntg_hour[4:6]}+09:00"
                )
            except Exception:
                market_timestamp = None

        fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        return KisCurrentQuote(
            ticker=ticker,
            source="KIS",
            market=market,
            currency="KRW",
            price=price,
            change=change,
            change_rate_pct=change_rate_pct,
            volume=volume,
            market_timestamp=market_timestamp,
            fetched_at=fetched_at,
            status="success",
        )

    def get_historical_candles(
        self,
        ticker: str,
        period: str,
        start_date: date,
        end_date: date,
        adjust: bool = True,
    ) -> KisHistoricalQuotes:
        """Fetch historical OHLCV candles (Day, Week, Month) for a Korean stock."""
        ticker = ticker.strip()
        if not re.match(r"^\d{6}$", ticker):
            raise ValueError(f"Ticker must be a 6-digit numeric code, got: {ticker}")

        period = period.upper().strip()
        if period not in ("D", "W", "M"):
            raise ValueError(f"Unsupported period '{period}'. Must be 'D', 'W', or 'M'")

        if start_date > end_date:
            raise ValueError(
                f"start_date ({start_date}) must be before or equal to end_date ({end_date})"
            )

        data = self._execute_request_with_retry(
            endpoint="/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            tr_id="FHKST03010100",
            params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": ticker,
                "FID_INPUT_DATE_1": start_date.strftime("%Y%m%d"),
                "FID_INPUT_DATE_2": end_date.strftime("%Y%m%d"),
                "FID_PERIOD_DIV_CODE": period,
                "FID_ORG_ADJ_PRC": "0" if adjust else "1",
            },
        )

        output2 = data.get("output2")
        fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        adjustment_str = "adjusted" if adjust else "unadjusted"

        if not output2 or not isinstance(output2, list):
            return KisHistoricalQuotes(
                ticker=ticker,
                source="KIS",
                period=period,
                adjustment=adjustment_str,
                currency="KRW",
                start_date=start_date.isoformat(),
                end_date=end_date.isoformat(),
                count=0,
                candles=[],
                fetched_at=fetched_at,
                status="no_data",
            )

        candles_by_date: dict[str, KisCandle] = {}
        for item in output2:
            raw_date = str(item.get("stck_bsop_date") or "").strip()
            if len(raw_date) != 8:
                continue

            iso_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
            # Filter to requested range bounds
            try:
                c_date = date.fromisoformat(iso_date)
            except ValueError:
                continue
            if c_date < start_date or c_date > end_date:
                continue

            try:
                open_val = int(float(item.get("stck_oprc", 0)))
                high_val = int(float(item.get("stck_hgpr", 0)))
                low_val = int(float(item.get("stck_lwpr", 0)))
                close_val = int(float(item.get("stck_clpr", 0)))
                volume_val = int(float(item.get("acml_vol", 0)))
                raw_value = item.get("acml_tr_pbmn")
                value_val = int(float(raw_value)) if raw_value and float(raw_value) > 0 else None
            except (ValueError, TypeError):
                continue

            # Ensure valid bounds
            if low_val > high_val:
                low_val, high_val = high_val, low_val

            candles_by_date[iso_date] = KisCandle(
                date=iso_date,
                open=open_val,
                high=high_val,
                low=low_val,
                close=close_val,
                volume=volume_val,
                value=value_val,
            )

        # Sort ascending by date
        sorted_candles = [candles_by_date[d] for d in sorted(candles_by_date.keys())]

        status = "success" if sorted_candles else "no_data"

        return KisHistoricalQuotes(
            ticker=ticker,
            source="KIS",
            period=period,
            adjustment=adjustment_str,
            currency="KRW",
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            count=len(sorted_candles),
            candles=sorted_candles,
            fetched_at=fetched_at,
            status=status,
        )
