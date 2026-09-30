from datetime import date

import httpx
import pytest

from app.adapters.base import DataNotFoundError
from app.adapters.yahoo_adapter import YahooFinanceAdapter


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "request failed",
                request=httpx.Request("GET", "https://example.test"),
                response=httpx.Response(self.status_code),
            )

    def json(self):
        return self.payload


def test_yahoo_adapter_parses_korean_ohlcv(monkeypatch):
    payload = {
        "chart": {
            "result": [{
                "meta": {"gmtoffset": 32400},
                "timestamp": [1704157200],
                "indicators": {
                    "quote": [{
                        "open": [70000.0],
                        "high": [71000.0],
                        "low": [69000.0],
                        "close": [70500.0],
                        "volume": [123456],
                    }],
                    "adjclose": [{"adjclose": [70400.0]}],
                },
            }],
            "error": None,
        }
    }
    monkeypatch.setattr(
        "app.adapters.yahoo_adapter.httpx.get",
        lambda *args, **kwargs: FakeResponse(payload),
    )

    records = YahooFinanceAdapter().fetch_ohlcv(
        "005930",
        date(2024, 1, 2),
        date(2024, 1, 2),
        "UNKNOWN",
    )

    assert len(records) == 1
    assert records[0].ticker == "005930"
    assert records[0].trade_date == date(2024, 1, 2)
    assert records[0].close == 70500.0
    assert records[0].adjusted_close == 70400.0
    assert records[0].volume == 123456
    assert len(records[0].raw_hash) == 64


def test_yahoo_adapter_tries_both_markets_for_unknown(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        if url.endswith(".KS"):
            return FakeResponse({}, status_code=404)
        return FakeResponse({
            "chart": {
                "result": [{
                    "meta": {"gmtoffset": 32400},
                    "timestamp": [1704157200],
                    "indicators": {
                        "quote": [{
                            "open": [1],
                            "high": [1],
                            "low": [1],
                            "close": [1],
                            "volume": [1],
                        }],
                    },
                }],
            }
        })

    monkeypatch.setattr("app.adapters.yahoo_adapter.httpx.get", fake_get)
    records = YahooFinanceAdapter().fetch_ohlcv(
        "247540",
        date(2024, 1, 2),
        date(2024, 1, 2),
        "UNKNOWN",
    )

    assert len(records) == 1
    assert calls[0].endswith("247540.KS")
    assert calls[1].endswith("247540.KQ")


def test_yahoo_adapter_rejects_empty_prices(monkeypatch):
    monkeypatch.setattr(
        "app.adapters.yahoo_adapter.httpx.get",
        lambda *args, **kwargs: FakeResponse({}, status_code=404),
    )

    with pytest.raises(DataNotFoundError):
        YahooFinanceAdapter().fetch_ohlcv(
            "999999",
            date(2024, 1, 2),
            date(2024, 1, 2),
            "UNKNOWN",
        )

def test_yahoo_adapter_uses_index_for_trading_days(monkeypatch):
    payload = {
        "chart": {
            "result": [{
                "meta": {"gmtoffset": 32400},
                "timestamp": [1704157200, 1704243600],
                "indicators": {},
            }],
        }
    }
    monkeypatch.setattr(
        "app.adapters.yahoo_adapter.httpx.get",
        lambda *args, **kwargs: FakeResponse(payload),
    )

    days = YahooFinanceAdapter().get_trading_days(
        date(2024, 1, 2),
        date(2024, 1, 3),
    )

    assert days == [date(2024, 1, 2), date(2024, 1, 3)]
