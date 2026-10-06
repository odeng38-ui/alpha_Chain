from datetime import date

import pandas as pd

from app.adapters.pykrx_adapter import PykrxAdapter


def test_fetch_ohlcv_falls_back_to_adjusted_close_when_raw_is_empty(monkeypatch):
    index = pd.to_datetime(["2024-01-02"])
    adjusted = pd.DataFrame({
        "시가": [100.0],
        "고가": [110.0],
        "저가": [90.0],
        "종가": [105.0],
        "거래량": [1000],
    }, index=index)
    empty = pd.DataFrame()
    responses = iter([adjusted, empty])
    adapter = PykrxAdapter(request_delay=0)
    monkeypatch.setattr(
        adapter,
        "_call_with_retry",
        lambda *args, **kwargs: next(responses),
    )

    records = adapter.fetch_ohlcv(
        "043220",
        date(2024, 1, 2),
        date(2024, 1, 2),
        "KOSDAQ",
    )

    assert len(records) == 1
    assert records[0].close == 105.0
    assert records[0].adjusted_close == 105.0