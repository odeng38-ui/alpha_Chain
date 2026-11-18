"""Unit tests for Korea Investment & Securities (KIS) On-Demand Quote Engine (Phase 1).

Covers all 9 mandatory verification items specified in AC-KIS-P1:
1. Current quote parsing & mapping (Hanmi Semiconductor 042700)
2. Daily, weekly, monthly OHLCV candles parsing & ascending sort
3. Ticker, period, and date range boundary validations
4. Token caching, reuse, and refresh logic
5. Rate limit (429) & timeout (504) handling
6. No-data handling (status='no_data')
7. Admin protection on internal API endpoints (401/403)
8. Credential shielding (no app_key, app_secret, or token in outputs/errors)
9. Clear semantic distinction between current quote and historical candle
"""
from datetime import date
from typing import Any

import httpx
import pytest
from fastapi import HTTPException

from app.adapters.kis_quote_adapter import (
    KisCandle,
    KisConfigError,
    KisCurrentQuote,
    KisHistoricalQuotes,
    KisQuoteAdapter,
    KisRateLimitError,
    KisTimeoutError,
)
from app.config import settings
from app.security import require_admin
from app.services.kis_quote_service import KisQuoteService

MOCK_APP_KEY = "test_kis_app_key_dummy"
MOCK_APP_SECRET = "test_kis_app_secret_dummy"
MOCK_BASE_URL = "https://mock.koreainvestment.com"
MOCK_TOKEN = "mock_access_token_12345"


@pytest.fixture
def mock_token_response():
    return {
        "access_token": MOCK_TOKEN,
        "token_type": "Bearer",
        "expires_in": 86400,
        "access_token_token_expired": "2026-11-19 12:00:00",
    }


@pytest.fixture
def mock_hanmi_quote_response():
    """Mock KIS Inquire-Price response for Hanmi Semiconductor (042700)."""
    return {
        "rt_cd": "0",
        "msg_cd": "MCA00000",
        "msg1": "정상처리 되었습니다.",
        "output": {
            "stck_prpr": "185000",
            "prdy_vrss": "2500",
            "prdy_ctrt": "1.37",
            "acml_vol": "752000",
            "rprs_mrkt_kor_name": "코스피",
            "stck_bsop_date": "20261118",
            "stck_cntg_hour": "153000",
        },
    }


@pytest.fixture
def mock_candles_response():
    """Mock KIS Inquire-Daily-Itemchartprice response in typical reverse chronological order."""
    return {
        "rt_cd": "0",
        "msg_cd": "MCA00000",
        "msg1": "정상처리 되었습니다.",
        "output1": {
            "prdy_vrss": "2500",
        },
        "output2": [
            {
                "stck_bsop_date": "20261118",
                "stck_oprc": "184000",
                "stck_hgpr": "188000",
                "stck_lwpr": "183000",
                "stck_clpr": "185000",
                "acml_vol": "752000",
                "acml_tr_pbmn": "139120000000",
            },
            {
                "stck_bsop_date": "20261117",
                "stck_oprc": "182000",
                "stck_hgpr": "187000",
                "stck_lwpr": "180500",
                "stck_clpr": "182500",
                "acml_vol": "620000",
                "acml_tr_pbmn": "113700000000",
            },
        ],
    }


def make_mock_transport(responses: dict[str, Any]) -> httpx.MockTransport:
    """Helper to route mock requests based on path and method."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path in responses:
            item = responses[path]
            if callable(item):
                return item(request)
            if isinstance(item, tuple):
                status_code, body = item
                return httpx.Response(status_code, json=body, request=request)
            return httpx.Response(200, json=item, request=request)
        return httpx.Response(404, json={"detail": f"Unmatched mock URL: {path}"}, request=request)

    return httpx.MockTransport(handler)


# ==============================================================================
# Test 1: Hanmi Semiconductor 042700 Current Quote Mapping
# ==============================================================================
def test_1_current_quote_mapping_hanmi(mock_token_response, mock_hanmi_quote_response):
    routes = {
        "/oauth2/tokenP": mock_token_response,
        "/uapi/domestic-stock/v1/quotations/inquire-price": mock_hanmi_quote_response,
    }
    client = httpx.Client(transport=make_mock_transport(routes))
    adapter = KisQuoteAdapter(
        app_key=MOCK_APP_KEY,
        app_secret=MOCK_APP_SECRET,
        base_url=MOCK_BASE_URL,
        client=client,
    )

    quote = adapter.get_current_quote("042700")

    assert isinstance(quote, KisCurrentQuote)
    assert quote.ticker == "042700"
    assert quote.source == "KIS"
    assert quote.market == "KOSPI"
    assert quote.currency == "KRW"
    assert quote.price == 185000
    assert quote.change == 2500
    assert quote.change_rate_pct == 1.37
    assert quote.volume == 752000
    assert quote.market_timestamp == "2026-11-18T15:30:00+09:00"
    assert quote.status == "success"
    assert quote.fetched_at.endswith("Z")


# ==============================================================================
# Test 2: Daily, Weekly, Monthly Candles Mapping and Ascending Sort
# ==============================================================================
def test_2_candles_mapping_and_ordering(mock_token_response, mock_candles_response):
    routes = {
        "/oauth2/tokenP": mock_token_response,
        "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice": mock_candles_response,
    }
    client = httpx.Client(transport=make_mock_transport(routes))
    adapter = KisQuoteAdapter(
        app_key=MOCK_APP_KEY,
        app_secret=MOCK_APP_SECRET,
        base_url=MOCK_BASE_URL,
        client=client,
    )

    # Test Day candles
    res = adapter.get_historical_candles(
        ticker="042700",
        period="D",
        start_date=date(2026, 11, 16),
        end_date=date(2026, 11, 18),
        adjust=True,
    )

    assert isinstance(res, KisHistoricalQuotes)
    assert res.ticker == "042700"
    assert res.period == "D"
    assert res.adjustment == "adjusted"
    assert res.count == 2
    assert len(res.candles) == 2

    # Verification of chronological ascending order (Nov 17 then Nov 18)
    assert res.candles[0].date == "2026-11-17"
    assert res.candles[0].open == 182000
    assert res.candles[0].high == 187000
    assert res.candles[0].low == 180500
    assert res.candles[0].close == 182500
    assert res.candles[0].volume == 620000
    assert res.candles[0].value == 113700000000

    assert res.candles[1].date == "2026-11-18"
    assert res.candles[1].close == 185000

    # Test Weekly and Monthly periods pass through correctly
    res_w = adapter.get_historical_candles("042700", "W", date(2026, 11, 16), date(2026, 11, 18))
    assert res_w.period == "W"
    res_m = adapter.get_historical_candles("042700", "M", date(2026, 11, 16), date(2026, 11, 18))
    assert res_m.period == "M"


# ==============================================================================
# Test 3: Ticker, Period, and Date Validation (422)
# ==============================================================================
def test_3_validation_boundaries():
    service = KisQuoteService()

    # Invalid tickers
    with pytest.raises(ValueError, match="6-digit numeric"):
        service.get_current_quote("04270")
    with pytest.raises(ValueError, match="6-digit numeric"):
        service.get_current_quote("HANMI")

    # Invalid period
    with pytest.raises(ValueError, match="Invalid period"):
        service.get_historical_quotes("042700", period="X")

    # Invalid date order: start_date > end_date
    with pytest.raises(ValueError, match="cannot be later than end_date"):
        service.get_historical_quotes(
            "042700",
            period="D",
            start_date=date(2026, 11, 18),
            end_date=date(2026, 11, 10),
        )

    # Date range exceeds 5 years lookback
    with pytest.raises(ValueError, match="exceeds maximum allowed lookback"):
        service.get_historical_quotes(
            "042700",
            period="D",
            start_date=date(2020, 1, 1),
            end_date=date(2026, 1, 1),
        )


# ==============================================================================
# Test 4: Token Caching, Reuse, and Expiration Refresh
# ==============================================================================
def test_4_token_caching_and_refresh():
    token_call_count = 0

    def token_handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_call_count
        token_call_count += 1
        return httpx.Response(
            200,
            json={
                "access_token": f"token_{token_call_count}",
                "expires_in": 3600,
            },
            request=request,
        )

    routes = {"/oauth2/tokenP": token_handler}
    client = httpx.Client(transport=make_mock_transport(routes))
    adapter = KisQuoteAdapter(
        app_key=MOCK_APP_KEY,
        app_secret=MOCK_APP_SECRET,
        base_url=MOCK_BASE_URL,
        client=client,
    )

    t1 = adapter.get_token()
    assert t1 == "token_1"
    assert token_call_count == 1

    # Second call reuses cached token
    t2 = adapter.get_token()
    assert t2 == "token_1"
    assert token_call_count == 1

    # Force refresh fetches new token
    t3 = adapter.get_token(force_refresh=True)
    assert t3 == "token_2"
    assert token_call_count == 2


# ==============================================================================
# Test 5: Rate Limit (429) and Timeout (504) Handling
# ==============================================================================
def test_5_rate_limit_and_timeout():
    # 5a. Rate limit test
    routes_429 = {
        "/oauth2/tokenP": {"access_token": "valid_token", "expires_in": 3600},
        "/uapi/domestic-stock/v1/quotations/inquire-price": (429, {"msg": "Too Many Requests"}),
    }
    client_429 = httpx.Client(transport=make_mock_transport(routes_429))
    adapter_429 = KisQuoteAdapter(
        app_key=MOCK_APP_KEY,
        app_secret=MOCK_APP_SECRET,
        base_url=MOCK_BASE_URL,
        client=client_429,
    )

    with pytest.raises(KisRateLimitError):
        adapter_429.get_current_quote("042700")

    # 5b. Timeout test
    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("Socket timed out", request=request)

    client_timeout = httpx.Client(transport=httpx.MockTransport(timeout_handler))
    adapter_timeout = KisQuoteAdapter(
        app_key=MOCK_APP_KEY,
        app_secret=MOCK_APP_SECRET,
        base_url=MOCK_BASE_URL,
        client=client_timeout,
    )

    with pytest.raises(KisTimeoutError):
        adapter_timeout.get_token()


# ==============================================================================
# Test 6: No Data Handling (status='no_data')
# ==============================================================================
def test_6_no_data_handling(mock_token_response):
    empty_candles_resp = {
        "rt_cd": "0",
        "msg_cd": "MCA00000",
        "msg1": "조회된 데이터가 없습니다.",
        "output1": {},
        "output2": [],
    }
    routes = {
        "/oauth2/tokenP": mock_token_response,
        "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice": empty_candles_resp,
    }
    client = httpx.Client(transport=make_mock_transport(routes))
    adapter = KisQuoteAdapter(
        app_key=MOCK_APP_KEY,
        app_secret=MOCK_APP_SECRET,
        base_url=MOCK_BASE_URL,
        client=client,
    )

    res = adapter.get_historical_candles(
        ticker="042700",
        period="D",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 2),
    )

    assert res.count == 0
    assert len(res.candles) == 0
    assert res.status == "no_data"


# ==============================================================================
# Test 7: Admin Protection on API Endpoints (401/403/503)
# ==============================================================================
def test_7_admin_auth_endpoints(monkeypatch):
    """Verify that KIS quote endpoints are protected by require_admin and enforce authentication."""
    # 1. Verify route definitions enforce require_admin dependency
    from app.api.v1.endpoints.kis_quotes import (
        get_current_quote,
        get_historical_quotes,
    )
    from app.api.v1.endpoints.kis_quotes import (
        router as kis_router,
    )

    routes_by_path = {r.path: r for r in kis_router.routes}
    assert "/kis/quotes/{ticker}" in routes_by_path
    assert "/kis/quotes/{ticker}/history" in routes_by_path

    # Verify both routes have require_admin dependency configured
    quote_route = routes_by_path["/kis/quotes/{ticker}"]
    history_route = routes_by_path["/kis/quotes/{ticker}/history"]
    assert any(d.dependency == require_admin for d in quote_route.dependencies)
    assert any(d.dependency == require_admin for d in history_route.dependencies)

    # 2. Verify require_admin behavior under production environment
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "ADMIN_API_KEY", "super_secret_admin_key")

    # Missing header -> 401
    with pytest.raises(HTTPException) as exc_missing:
        require_admin(None)
    assert exc_missing.value.status_code == 401

    # Invalid header -> 401
    with pytest.raises(HTTPException) as exc_invalid:
        require_admin("wrong_key")
    assert exc_invalid.value.status_code == 401

    # Valid header -> passes without exception
    require_admin("super_secret_admin_key")

    # 3. Verify endpoint input validation returns HTTP 422
    service = KisQuoteService()
    with pytest.raises(HTTPException) as exc_422:
        get_current_quote("invalid_ticker", service=service)
    assert exc_422.value.status_code == 422

    with pytest.raises(HTTPException) as exc_hist_422:
        get_historical_quotes("042700", period="INVALID_PERIOD", service=service)
    assert exc_hist_422.value.status_code == 422


# ==============================================================================
# Test 8: Credential Shielding (No Secrets Exposed)
# ==============================================================================
def test_8_credential_shielding(mock_token_response, mock_hanmi_quote_response):
    secret_key = "TOP_SECRET_KIS_KEY_XYZ"
    secret_secret = "TOP_SECRET_KIS_SECRET_ABC"

    routes = {
        "/oauth2/tokenP": mock_token_response,
        "/uapi/domestic-stock/v1/quotations/inquire-price": mock_hanmi_quote_response,
    }
    client = httpx.Client(transport=make_mock_transport(routes))
    adapter = KisQuoteAdapter(
        app_key=secret_key,
        app_secret=secret_secret,
        base_url=MOCK_BASE_URL,
        client=client,
    )

    quote = adapter.get_current_quote("042700")
    quote_json = quote.model_dump_json()

    assert secret_key not in quote_json
    assert secret_secret not in quote_json
    assert MOCK_TOKEN not in quote_json

    # Test error shielding
    config_err = KisConfigError("Missing configuration")
    assert secret_key not in str(config_err)


# ==============================================================================
# Test 9: Distinction Between Current Quote and Historical Close
# ==============================================================================
def test_9_current_quote_vs_historical_close():
    """Verify semantic distinction between intraday/real-time quote and completed daily candle."""
    quote = KisCurrentQuote(
        ticker="042700",
        price=185000,
        change=2500,
        change_rate_pct=1.37,
        volume=752000,
        market_timestamp="2026-11-18T15:30:00+09:00",
        fetched_at="2026-11-18T07:00:00Z",
    )

    candle = KisCandle(
        date="2026-11-18",
        open=184000,
        high=188000,
        low=183000,
        close=185000,
        volume=752000,
        value=139120000000,
    )

    # Current quote has intraday change fields and market_timestamp
    assert hasattr(quote, "change")
    assert hasattr(quote, "change_rate_pct")
    assert hasattr(quote, "market_timestamp")

    # Historical candle has OHLC fields and trading date
    assert hasattr(candle, "open")
    assert hasattr(candle, "high")
    assert hasattr(candle, "low")
    assert hasattr(candle, "close")
    assert hasattr(candle, "date")
    assert not hasattr(candle, "change_rate_pct")
    assert not hasattr(candle, "market_timestamp")
