"""
PykrxAdapter — KRX 공개 데이터를 pykrx 라이브러리로 수집하는 어댑터.

pykrx: https://github.com/sharebook-kr/pykrx
- 무료, KRX 공개 데이터 기반
- 수정주가 옵션 지원
- rate limit: 너무 빠른 반복 호출 시 KRX가 차단 → 재시도 + 지수 백오프 적용

타임존:
- pykrx 반환 날짜는 KST 기준 거래일
- DB 저장 시 trade_date는 KST date (timezone-aware 없이 date 타입)
"""

import hashlib
import json
import logging
import time
from datetime import date, timedelta
from typing import List, Optional

from app.adapters.base import (
    AdapterError,
    BrokerAdapter,
    OHLCVRecord,
)

logger = logging.getLogger(__name__)

# pykrx는 선택적 의존성 — import 실패 시 명확한 오류 표시
try:
    from pykrx import stock as krx_stock
    PYKRX_AVAILABLE = True
except ImportError:
    PYKRX_AVAILABLE = False
    krx_stock = None


class PykrxAdapter(BrokerAdapter):
    """
    pykrx 기반 KRX 시세 어댑터.

    Args:
        request_delay: 각 API 호출 사이 최소 대기 시간(초). KRX 차단 방지.
        max_retries: 실패 시 최대 재시도 횟수
        retry_base_delay: 지수 백오프 기본 대기 시간(초)
    """

    def __init__(
        self,
        request_delay: float = 0.3,
        max_retries: int = 3,
        retry_base_delay: float = 2.0,
    ):
        if not PYKRX_AVAILABLE:
            raise ImportError(
                "pykrx가 설치되어 있지 않습니다. "
                "`pip install pykrx` 를 실행하세요."
            )
        self.request_delay = request_delay
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay
        self._last_call_time: float = 0.0
        self._business_day_cache = {}

    @property
    def name(self) -> str:
        return "pykrx"

    # ------------------------------------------------------------------ #
    #  퍼블릭 메서드                                                        #
    # ------------------------------------------------------------------ #

    def fetch_ohlcv(
        self,
        ticker: str,
        start_date: date,
        end_date: date,
        market: str = "KOSPI",
    ) -> List[OHLCVRecord]:
        """기간별 일봉 OHLCV 수집 (수정주가 포함)."""
        start_str = start_date.strftime("%Y%m%d")
        end_str = end_date.strftime("%Y%m%d")

        logger.debug(
            "pykrx fetch: ticker=%s start=%s end=%s market=%s",
            ticker, start_str, end_str, market,
        )

        # 수정주가 포함 OHLCV
        raw_adj = self._call_with_retry(
            krx_stock.get_market_ohlcv_by_date,
            start_str, end_str, ticker,
            adjusted=True,
        )
        # 원시 종가(비수정) 별도 호출 — 원본 보존을 위해
        raw_orig = self._call_with_retry(
            krx_stock.get_market_ohlcv_by_date,
            start_str, end_str, ticker,
            adjusted=False,
        )

        if raw_adj is None or raw_adj.empty:
            logger.info("데이터 없음: ticker=%s %s~%s", ticker, start_str, end_str)
            return []

        records: List[OHLCVRecord] = []
        for idx in raw_adj.index:
            row_adj = raw_adj.loc[idx]
            row_orig = raw_orig.loc[idx] if (raw_orig is not None and idx in raw_orig.index) else None

            trade_date_obj = idx.date() if hasattr(idx, "date") else idx

            # 원본 해시 계산 (재현성)
            raw_dict = {
                "ticker": ticker,
                "date": str(trade_date_obj),
                "adj_open": float(row_adj.get("시가", 0) or 0),
                "adj_high": float(row_adj.get("고가", 0) or 0),
                "adj_low": float(row_adj.get("저가", 0) or 0),
                "adj_close": float(row_adj.get("종가", 0) or 0),
                "volume": int(row_adj.get("거래량", 0) or 0),
            }
            raw_hash = hashlib.sha256(
                json.dumps(raw_dict, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()

            orig_close = None
            if row_orig is not None:
                orig_close_val = row_orig.get("종가")
                orig_close = float(orig_close_val) if orig_close_val and orig_close_val != 0 else None

            adj_close_val = row_adj.get("종가")
            adj_close = float(adj_close_val) if adj_close_val and adj_close_val != 0 else None

            # 거래대금: pykrx 컬럼명 '거래대금'
            value_val = row_adj.get("거래대금")
            value = float(value_val) if value_val and value_val != 0 else None

            records.append(OHLCVRecord(
                ticker=ticker,
                trade_date=trade_date_obj,
                open=float(row_adj.get("시가") or 0) or None,
                high=float(row_adj.get("고가") or 0) or None,
                low=float(row_adj.get("저가") or 0) or None,
                close=orig_close,           # 비수정 종가
                volume=int(row_adj.get("거래량") or 0) or None,
                value=value,
                adjusted_close=adj_close,   # 수정주가
                raw_hash=raw_hash,
            ))

        return records

    def fetch_latest(
        self,
        ticker: str,
        market: str = "KOSPI",
    ) -> Optional[OHLCVRecord]:
        """가장 최근 거래일 단건 조회."""
        today = date.today()
        start = today - timedelta(days=5)  # 최대 5일 전까지 탐색 (주말/공휴일 대비)
        records = self.fetch_ohlcv(ticker, start, today, market)
        return records[-1] if records else None

    def get_trading_days(
        self,
        start_date: date,
        end_date: date,
        market: str = "KOSPI",
    ) -> List[date]:
        """지정 기간의 실제 거래일 목록 반환."""
        result = []
        year, month = start_date.year, start_date.month
        while (year, month) <= (end_date.year, end_date.month):
            cache_key = (year, month)
            if cache_key not in self._business_day_cache:
                self._business_day_cache[cache_key] = self._call_with_retry(
                    krx_stock.get_business_days, year, month
                )
            days = self._business_day_cache[cache_key]
            for value in days or []:
                if hasattr(value, "date"):
                    parsed = value.date()
                elif isinstance(value, date):
                    parsed = value
                else:
                    parsed = date.fromisoformat(str(value).replace("/", "-")[:10])
                if start_date <= parsed <= end_date:
                    result.append(parsed)
            month += 1
            if month == 13:
                year += 1
                month = 1
        return sorted(set(result))

    # ------------------------------------------------------------------ #
    #  내부 헬퍼                                                            #
    # ------------------------------------------------------------------ #

    def _rate_limit(self):
        """최소 호출 간격 유지 (KRX 차단 방지)."""
        elapsed = time.time() - self._last_call_time
        if elapsed < self.request_delay:
            time.sleep(self.request_delay - elapsed)
        self._last_call_time = time.time()

    def _call_with_retry(self, fn, *args, **kwargs):
        """
        지수 백오프 재시도 래퍼.

        Raises:
            RateLimitError: 최대 재시도 후에도 실패
            AdapterError: 기타 오류
        """
        last_exc = None
        for attempt in range(self.max_retries):
            try:
                self._rate_limit()
                return fn(*args, **kwargs)
            except Exception as exc:
                last_exc = exc
                wait = self.retry_base_delay * (2 ** attempt)
                logger.warning(
                    "pykrx 호출 실패 (시도 %d/%d): %s — %.1f초 후 재시도",
                    attempt + 1, self.max_retries, exc, wait,
                )
                time.sleep(wait)

        raise AdapterError(
            f"pykrx 최대 재시도({self.max_retries}회) 초과: {last_exc}"
        ) from last_exc
