"""
BrokerAdapter 추상 인터페이스

어댑터 패턴으로 시세 공급자를 교체 가능하게 한다.
현재 구현체: PykrxAdapter (KRX 공개 데이터)
향후 교체 가능: 한투 API, 키움 API, 인포스탁 등
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from typing import List, Optional


@dataclass
class OHLCVRecord:
    """
    표준 일봉 데이터 레코드.
    어댑터는 공급자별 응답을 이 포맷으로 정규화해 반환해야 한다.
    """
    ticker: str
    trade_date: date
    open: Optional[float]
    high: Optional[float]
    low: Optional[float]
    close: Optional[float]
    volume: Optional[int]
    value: Optional[float]               # 거래대금 (원)
    adjusted_close: Optional[float]      # 수정주가
    raw_hash: Optional[str] = None       # 원본 응답 SHA-256 해시 (재현성 보존)


class BrokerAdapter(ABC):
    """
    시세 공급자 추상 어댑터.
    모든 구현체는 이 인터페이스를 따른다.
    """

    @abstractmethod
    def fetch_ohlcv(
        self,
        ticker: str,
        start_date: date,
        end_date: date,
        market: str = "KOSPI",
    ) -> List[OHLCVRecord]:
        """
        지정 종목의 기간별 일봉 OHLCV를 가져온다.

        Args:
            ticker: 종목코드 (예: '005930')
            start_date: 조회 시작일 (포함)
            end_date: 조회 종료일 (포함)
            market: 'KOSPI' 또는 'KOSDAQ'

        Returns:
            OHLCVRecord 리스트 (날짜 오름차순)

        Raises:
            AdapterError: 조회 실패 또는 파싱 오류
        """

    @abstractmethod
    def fetch_latest(
        self,
        ticker: str,
        market: str = "KOSPI",
    ) -> Optional[OHLCVRecord]:
        """
        지정 종목의 가장 최근 거래일 데이터를 가져온다.

        Returns:
            OHLCVRecord 또는 데이터가 없으면 None
        """

    @abstractmethod
    def get_trading_days(
        self,
        start_date: date,
        end_date: date,
        market: str = "KOSPI",
    ) -> List[date]:
        """
        지정 기간의 실제 거래일(시장 휴장일 제외) 목록을 반환한다.

        Returns:
            거래일 date 리스트 (오름차순)
        """

    @property
    @abstractmethod
    def name(self) -> str:
        """어댑터 이름. 로그/버전 추적에 사용."""


class AdapterError(Exception):
    """시세 어댑터 오류 기본 클래스"""
    pass


class RateLimitError(AdapterError):
    """API 호출 한도 초과"""
    pass


class DataNotFoundError(AdapterError):
    """요청한 데이터가 없음"""
    pass
