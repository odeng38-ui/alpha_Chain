"""
PriceQualityService — 일봉 데이터 품질 자동 검사.

검사 항목:
1. 중복: (security_id, trade_date) PK 수준에서 중복 카운트
2. 결측: 실제 거래일 대비 적재된 행 수 비율
3. 가격 급변: 전일 대비 ±30% 초과 (분할/병합 미처리 가능성)
4. 음수 거래량: volume < 0
5. 수정주가 불일치: adjusted_close가 close 대비 비정상 범위
"""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, case, func
from sqlalchemy.orm import Session

from app.adapters.base import BrokerAdapter
from app.adapters.pykrx_adapter import PykrxAdapter
from app.models.schema import DailyPrice, Security

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
# 결과 데이터 클래스                                                      #
# ------------------------------------------------------------------ #

@dataclass
class DuplicateIssue:
    security_id: int
    ticker: str
    trade_date: date
    count: int


@dataclass
class MissingDateIssue:
    security_id: int
    ticker: str
    missing_date: date


@dataclass
class PriceSpikeIssue:
    security_id: int
    ticker: str
    trade_date: date
    prev_close: float
    curr_close: float
    change_pct: float


@dataclass
class NegativeVolumeIssue:
    security_id: int
    ticker: str
    trade_date: date
    volume: int


@dataclass
class AdjustedCloseMismatch:
    security_id: int
    ticker: str
    trade_date: date
    close: float
    adjusted_close: float
    ratio: float


@dataclass
class SecurityQualityReport:
    security_id: int
    ticker: str
    market: str
    total_rows: int
    expected_trading_days: int
    coverage_pct: float          # 적재율 (%)
    duplicates: List[DuplicateIssue] = field(default_factory=list)
    missing_dates: List[MissingDateIssue] = field(default_factory=list)
    price_spikes: List[PriceSpikeIssue] = field(default_factory=list)
    negative_volumes: List[NegativeVolumeIssue] = field(default_factory=list)
    adjusted_mismatches: List[AdjustedCloseMismatch] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "security_id": self.security_id,
            "ticker": self.ticker,
            "market": self.market,
            "total_rows": self.total_rows,
            "expected_trading_days": self.expected_trading_days,
            "coverage_pct": round(self.coverage_pct, 2),
            "duplicate_count": len(self.duplicates),
            "missing_count": len(self.missing_dates),
            "spike_count": len(self.price_spikes),
            "negative_volume_count": len(self.negative_volumes),
            "adjusted_mismatch_count": len(self.adjusted_mismatches),
            "issues": {
                "duplicates": [
                    {"trade_date": str(d.trade_date), "count": d.count}
                    for d in self.duplicates
                ],
                "missing_dates": [str(m.missing_date) for m in self.missing_dates],
                "price_spikes": [
                    {
                        "trade_date": str(s.trade_date),
                        "prev_close": s.prev_close,
                        "curr_close": s.curr_close,
                        "change_pct": round(s.change_pct, 2),
                    }
                    for s in self.price_spikes
                ],
                "negative_volumes": [
                    {"trade_date": str(n.trade_date), "volume": n.volume}
                    for n in self.negative_volumes
                ],
                "adjusted_mismatches": [
                    {
                        "trade_date": str(a.trade_date),
                        "close": a.close,
                        "adjusted_close": a.adjusted_close,
                        "ratio": round(a.ratio, 4),
                    }
                    for a in self.adjusted_mismatches
                ],
            },
        }


# ------------------------------------------------------------------ #
# 품질 검사 서비스                                                        #
# ------------------------------------------------------------------ #

class PriceQualityService:
    """
    Args:
        spike_threshold: 가격 급변 판단 임계값 (기본 30%)
        adj_ratio_min: 수정주가/원시주가 최소 비율 (기본 0.1)
        adj_ratio_max: 수정주가/원시주가 최대 비율 (기본 2.0)
        adapter: 거래일 조회용 어댑터 (결측 검사에 사용)
    """

    def __init__(
        self,
        spike_threshold: float = 0.30,
        adj_ratio_min: float = 0.1,
        adj_ratio_max: float = 2.0,
        adapter: Optional[BrokerAdapter] = None,
    ):
        self.spike_threshold = spike_threshold
        self.adj_ratio_min = adj_ratio_min
        self.adj_ratio_max = adj_ratio_max
        self._adapter = adapter

    @property
    def adapter(self) -> BrokerAdapter:
        if self._adapter is None:
            self._adapter = PykrxAdapter()
        return self._adapter

    def check_security(
        self,
        db: Session,
        security_id: int,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> SecurityQualityReport:
        """단일 종목 품질 검사."""
        security = db.query(Security).filter(Security.id == security_id).first()
        if not security:
            raise ValueError(f"Security ID {security_id} 를 찾을 수 없습니다.")

        # 기간 기본값: DB의 전체 보유 기간
        prices_query = db.query(DailyPrice).filter(
            DailyPrice.security_id == security_id
        )
        if start_date:
            prices_query = prices_query.filter(DailyPrice.trade_date >= start_date)
        if end_date:
            prices_query = prices_query.filter(DailyPrice.trade_date <= end_date)

        prices = prices_query.order_by(DailyPrice.trade_date).all()
        total_rows = len(prices)

        if total_rows == 0 and start_date is None and end_date is None:
            return SecurityQualityReport(
                security_id=security_id,
                ticker=security.ticker,
                market=security.market,
                total_rows=0,
                expected_trading_days=0,
                coverage_pct=0.0,
            )

        actual_start = start_date or prices[0].trade_date
        actual_end = end_date or prices[-1].trade_date

        # 실제 거래일 수 (어댑터 조회)
        try:
            trading_days = self.adapter.get_trading_days(
                actual_start, actual_end, security.market
            )
            expected = len(trading_days)
        except Exception as exc:
            logger.warning("거래일 조회 실패 — 결측 검사 스킵: %s", exc)
            trading_days = []
            expected = 0

        coverage_pct = min((total_rows / expected * 100), 100.0) if expected > 0 else 0.0

        report = SecurityQualityReport(
            security_id=security_id,
            ticker=security.ticker,
            market=security.market,
            total_rows=total_rows,
            expected_trading_days=expected,
            coverage_pct=coverage_pct,
        )

        # 1. 중복 검사
        report.duplicates = self._check_duplicates(db, security_id, security.ticker)

        # 2. 결측 검사
        if trading_days:
            existing_dates = {p.trade_date for p in prices}
            report.missing_dates = [
                MissingDateIssue(security_id, security.ticker, td)
                for td in trading_days
                if td not in existing_dates
            ]

        # 3. 가격 급변 검사
        report.price_spikes = self._check_price_spikes(prices, security_id, security.ticker)

        # 4. 음수 거래량 검사
        report.negative_volumes = [
            NegativeVolumeIssue(security_id, security.ticker, p.trade_date, int(p.volume))
            for p in prices
            if p.volume is not None and p.volume < 0
        ]

        # 5. 수정주가 불일치 검사
        report.adjusted_mismatches = self._check_adjusted_close(prices, security_id, security.ticker)

        return report

    def check_all(
        self,
        db: Session,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """Fast database-aggregate quality summary.

        Bulk monitoring avoids external exchange-calendar calls and ORM
        hydration of every daily-price row. Exact missing sessions and spike
        details remain available from check_security.
        """
        started_at = datetime.now()
        cutoff = date.today() - timedelta(days=365 * 5)
        securities = db.query(Security).filter(
            Security.security_type == "COMMON",
        ).limit(limit).all()
        security_ids = [security.id for security in securities]

        aggregates = {}
        if security_ids:
            mismatch = and_(
                DailyPrice.close.isnot(None),
                DailyPrice.close != 0,
                DailyPrice.adjusted_close.isnot(None),
                (
                    (DailyPrice.adjusted_close / DailyPrice.close < self.adj_ratio_min)
                    | (DailyPrice.adjusted_close / DailyPrice.close > self.adj_ratio_max)
                ),
            )
            rows = db.query(
                DailyPrice.security_id,
                func.count(DailyPrice.trade_date),
                func.min(DailyPrice.trade_date),
                func.max(DailyPrice.trade_date),
                func.sum(case((DailyPrice.volume < 0, 1), else_=0)),
                func.sum(case((mismatch, 1), else_=0)),
            ).filter(
                DailyPrice.security_id.in_(security_ids),
                DailyPrice.trade_date >= cutoff,
                DailyPrice.trade_date <= date.today(),
            ).group_by(DailyPrice.security_id).all()
            aggregates = {row[0]: row for row in rows}

        summary = {
            "mode": "bulk_database_aggregate",
            "total_securities_checked": len(securities),
            "securities_with_issues": 0,
            "total_duplicates": 0,
            "total_missing": 0,
            "total_spikes": 0,
            "total_negative_volumes": 0,
            "total_adj_mismatches": 0,
            "reports": [],
            "notes": {
                "missing": "weekday approximation; use the security detail endpoint for exchange-calendar exactness",
                "spikes": "not calculated in bulk mode",
            },
        }

        for security in securities:
            row = aggregates.get(security.id)
            total_rows = int(row[1]) if row else 0
            first_date = row[2] if row else None
            last_date = row[3] if row else None
            negative_count = int(row[4] or 0) if row else 0
            mismatch_count = int(row[5] or 0) if row else 0
            expected = 0
            if first_date and last_date:
                days = (last_date - first_date).days + 1
                expected = sum(
                    (first_date + timedelta(days=offset)).weekday() < 5
                    for offset in range(days)
                )
            missing_count = max(expected - total_rows, 0)
            issue_count = missing_count + negative_count + mismatch_count
            if issue_count:
                summary["securities_with_issues"] += 1
            summary["total_missing"] += missing_count
            summary["total_negative_volumes"] += negative_count
            summary["total_adj_mismatches"] += mismatch_count
            coverage = min(total_rows / expected * 100, 100.0) if expected else 0.0
            summary["reports"].append({
                "security_id": security.id,
                "ticker": security.ticker,
                "market": security.market,
                "total_rows": total_rows,
                "expected_trading_days": expected,
                "coverage_pct": round(coverage, 2),
                "duplicate_count": 0,
                "missing_count": missing_count,
                "spike_count": None,
                "negative_volume_count": negative_count,
                "adjusted_mismatch_count": mismatch_count,
            })

        summary["elapsed_ms"] = round(
            (datetime.now() - started_at).total_seconds() * 1000, 2
        )
        return summary
    # ------------------------------------------------------------------ #
    # 내부 검사 메서드                                                       #
    # ------------------------------------------------------------------ #

    def _check_duplicates(
        self, db: Session, security_id: int, ticker: str
    ) -> List[DuplicateIssue]:
        """PK 수준 중복 검사 (SQLite/PG 모두 지원)."""
        rows = (
            db.query(DailyPrice.trade_date, func.count(DailyPrice.trade_date).label("cnt"))
            .filter(DailyPrice.security_id == security_id)
            .group_by(DailyPrice.trade_date)
            .having(func.count(DailyPrice.trade_date) > 1)
            .all()
        )
        return [
            DuplicateIssue(security_id, ticker, row.trade_date, row.cnt)
            for row in rows
        ]

    def _check_price_spikes(
        self,
        prices: List[DailyPrice],
        security_id: int,
        ticker: str,
    ) -> List[PriceSpikeIssue]:
        """전일 대비 ±spike_threshold 초과 급변 탐지."""
        issues = []
        for i in range(1, len(prices)):
            prev = prices[i - 1]
            curr = prices[i]
            if prev.close is None or curr.close is None:
                continue
            prev_close = float(prev.close)
            curr_close = float(curr.close)
            if prev_close == 0:
                continue
            change_pct = (curr_close - prev_close) / prev_close
            if abs(change_pct) > self.spike_threshold:
                issues.append(PriceSpikeIssue(
                    security_id=security_id,
                    ticker=ticker,
                    trade_date=curr.trade_date,
                    prev_close=prev_close,
                    curr_close=curr_close,
                    change_pct=change_pct * 100,
                ))
        return issues

    def _check_adjusted_close(
        self,
        prices: List[DailyPrice],
        security_id: int,
        ticker: str,
    ) -> List[AdjustedCloseMismatch]:
        """수정주가가 원시주가 대비 비정상 범위인 경우 탐지."""
        issues = []
        for p in prices:
            if p.close is None or p.adjusted_close is None:
                continue
            close = float(p.close)
            adj = float(p.adjusted_close)
            if close == 0:
                continue
            ratio = adj / close
            if ratio < self.adj_ratio_min or ratio > self.adj_ratio_max:
                issues.append(AdjustedCloseMismatch(
                    security_id=security_id,
                    ticker=ticker,
                    trade_date=p.trade_date,
                    close=close,
                    adjusted_close=adj,
                    ratio=ratio,
                ))
        return issues


