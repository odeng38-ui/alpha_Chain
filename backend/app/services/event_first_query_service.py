"""Read-only Event-First view model assembled from existing stored results."""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy.orm import Session

from app.models.schema import CollectionCheckpoint, DailyPrice, ScoreSnapshot
from app.services.event_supply_chain_service import EventSupplyChainService


class EventFirstQueryError(ValueError):
    pass


class EventFirstNotFound(EventFirstQueryError):
    pass


class EventAnalysisProvider(Protocol):
    mode: str

    def analyze(self, db: Session, *, event_id: str, as_of: datetime) -> dict[str, Any]: ...


class StoredEventAnalysisProvider:
    mode = "stored_data"

    def __init__(self, service: EventSupplyChainService | None = None) -> None:
        self.service = service or EventSupplyChainService()

    def analyze(self, db: Session, *, event_id: str, as_of: datetime) -> dict[str, Any]:
        try:
            numeric_id = int(event_id)
        except (TypeError, ValueError) as exc:
            raise EventFirstNotFound("event not found") from exc
        return self.service.analyze(db, event_id=numeric_id, as_of=as_of)


def _number(value: Decimal | int | float | None) -> float | int | None:
    if isinstance(value, Decimal):
        return float(value)
    return value


class EventFirstQueryService:
    def __init__(
        self,
        provider: EventAnalysisProvider | None = None,
        *,
        stale_after_days: int = 7,
    ) -> None:
        if type(stale_after_days) is not int or stale_after_days < 0:
            raise ValueError("stale_after_days must be a non-negative integer")
        self.provider = provider or StoredEventAnalysisProvider()
        self.stale_after_days = stale_after_days

    def analyze(
        self,
        db: Session,
        *,
        event_id: str,
        as_of: datetime,
        score_horizon: str = "20d",
        score_version: str = "v1.0",
    ) -> dict[str, Any]:
        cutoff = self._utc(as_of)
        if db.new or db.dirty or db.deleted:
            raise EventFirstQueryError("clean read-only session required")
        with db.no_autoflush:
            analysis = self.provider.analyze(db, event_id=event_id, as_of=cutoff)
            event = analysis.get("event")
            if not isinstance(event, dict):
                raise EventFirstNotFound("event not found")
            event_time = None
            raw_time = event.get("available_at") or event.get("occurred_at")
            if isinstance(raw_time, datetime):
                event_time = raw_time
            elif isinstance(raw_time, str):
                try:
                    event_time = datetime.fromisoformat(raw_time)
                except ValueError:
                    event_time = None
            if event_time is None:
                event_time = cutoff

            candidates = tuple(
                self._candidate(db, row, cutoff, score_horizon, score_version, event_time=event_time)
                for row in analysis.get("candidates", ())
            )
        if db.new or db.dirty or db.deleted:
            raise EventFirstQueryError("read-only query mutated the session")
        return {
            "event": event,
            "anchor": self._anchor(analysis.get("anchors", ())),
            "analysis_context": {
                "mode": self.provider.mode,
                "as_of": cutoff.isoformat(),
                "score_horizon": score_horizon,
                "score_version": score_version,
            },
            "candidates": list(candidates),
            "excluded_candidates": analysis.get("excluded_companies", []),
            "event_evidence": analysis.get("event_evidence", []),
            "uncertainties": analysis.get("uncertainties", []),
            "empty_reasons": analysis.get("empty_reasons", []),
            "policy": {
                "investment_recommendation": False,
                "analysis_completed_is_fact_verified": False,
                "future_information_excluded": True,
            },
        }

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise EventFirstQueryError("as_of must be timezone-aware")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _anchor(rows: list[dict] | tuple[dict, ...]) -> dict | None:
        if not rows:
            return None
        row = rows[0]
        identifiers = {
            item.get("source"): item.get("value") for item in row.get("identifiers", ())
        }
        return {
            "company_id": row.get("company_id"),
            "company_name": row.get("legal_name") or row.get("name"),
            "cik": identifiers.get("CIK"),
            "verification_status": row.get("verification_status"),
        }

    def _candidate(
        self,
        db: Session,
        row: dict[str, Any],
        as_of: datetime,
        horizon: str,
        version: str,
        event_time: datetime | None = None,
    ) -> dict[str, Any]:
        securities = row.get("securities") or []
        security = securities[0] if securities else None
        paths = row.get("paths") or []
        evidence = [
            item
            for path in paths
            for relationship in path.get("relationships", ())
            for item in relationship.get("evidence", ())
        ]
        depth = min((path.get("tier") for path in paths if path.get("tier") is not None), default=None)
        security_id = security.get("security_id") if security else None
        return {
            "company_id": row.get("company_id"),
            "company_name": row.get("legal_name") or row.get("name"),
            "market": security.get("market") if security else None,
            "ticker": security.get("ticker") if security else None,
            "security_id": security_id,
            "relationship_depth": depth,
            "classification": row.get("classification", "supply_chain_candidate"),
            "eligibility_status": row.get(
                "eligibility_status", "pending_event_anchor_verification"
            ),
            "project_link_status": row.get("project_link_status", "unverified"),
            "valid_from": row.get("valid_from"),
            "valid_to": row.get("valid_to"),
            "evidence_status": row.get("evidence_status", "not_available"),
            "evidence": evidence or row.get("evidence", []),
            "paths": paths,
            "price": self._price(db, security_id, as_of),
            "alpha_score": self._score(db, security_id, as_of, horizon, version),
            "market_reaction": self._market_reaction(db, security_id, event_time or as_of, as_of),
            "analysis_states": {
                "relationship_deep_verification": "not_requested",
                "industry_impact": "not_requested",
                "backtest": "not_requested",
            },
        }

    def stored_market_snapshot(
        self, db: Session, *, security_id: int | None, as_of: datetime,
        horizon: str = "20d", version: str = "v1.0",
    ) -> dict[str, Any]:
        """Read existing prices/scores for an identified security; never calculate."""
        cutoff = self._utc(as_of)
        if db.new or db.dirty or db.deleted:
            raise EventFirstQueryError("clean read-only session required")
        with db.no_autoflush:
            price = self._price(db, security_id, cutoff)
            score = self._score(db, security_id, cutoff, horizon, version)
        checkpoint = None
        if security_id is not None:
            with db.no_autoflush:
                row = db.query(CollectionCheckpoint).filter(
                    CollectionCheckpoint.job_name == "daily_price",
                    CollectionCheckpoint.security_id == security_id).first()
                if row:
                    checkpoint = {"status": row.status,
                                  "last_success_date": row.last_success_date.isoformat() if row.last_success_date else None,
                                  "last_attempt_at": row.updated_at.isoformat() if row.updated_at else None}
        collection_status = "not_collected" if price["status"] == "not_available" else "incomplete" if any(price.get(field) is None or price[field] <= 0 for field in ("open", "high", "low", "close")) or price.get("volume") is None or price["volume"] < 0 else price["status"]
        from app.services.price_service import confirmed_non_trading_data
        non_trading = confirmed_non_trading_data(db, security_id, cutoff.date())
        if price.get("trade_date") in {day.isoformat() for day in non_trading["rows"]}:
            collection_status = "suspended_non_trading"
            price["market_status"] = "suspended_non_trading"
            price["analysis_eligible"] = False
        price["close_status"] = "available" if price.get("close") is not None else "not_available"
        if score.get("as_of_date"):
            age = (cutoff.date() - datetime.fromisoformat(score["as_of_date"]).date()).days
            score.update(age_days=age, freshness="stale" if age > self.stale_after_days else "available")
        return {"as_of": cutoff.isoformat(), "security_id": security_id,
                "horizon": horizon, "version": version,
                "stale_after_days": self.stale_after_days,
                "price": price, "alpha_score": score,
                "collection": {"status": collection_status, "checkpoint": checkpoint},
                "score_update_status": "not_computed" if score["status"] == "not_computed" else
                    "behind_price" if price.get("trade_date", "") > score.get("as_of_date", "") else
                    "waiting_for_price_update" if price["status"] == "stale" else "aligned"}

    def _price(self, db: Session, security_id: int | None, as_of: datetime) -> dict[str, Any]:
        if security_id is None:
            return {"status": "not_available"}
        row = (
            db.query(DailyPrice)
            .filter(DailyPrice.security_id == security_id, DailyPrice.trade_date <= as_of.date())
            .order_by(DailyPrice.trade_date.desc())
            .first()
        )
        if row is None:
            return {"status": "not_available"}
        age = (as_of.date() - row.trade_date).days
        return {
            "status": "stale" if age > self.stale_after_days else "available",
            "trade_date": row.trade_date.isoformat(),
            "age_days": age,
            "open": _number(row.open),
            "high": _number(row.high),
            "low": _number(row.low),
            "close": _number(row.close),
            "adjusted_close": _number(row.adjusted_close),
            "volume": row.volume,
        }

    @staticmethod
    def _score(
        db: Session,
        security_id: int | None,
        as_of: datetime,
        horizon: str,
        version: str,
    ) -> dict[str, Any]:
        if security_id is None:
            return {"status": "not_computed"}
        row = (
            db.query(ScoreSnapshot)
            .filter(
                ScoreSnapshot.security_id == security_id,
                ScoreSnapshot.as_of_date <= as_of.date(),
                ScoreSnapshot.horizon == horizon,
                ScoreSnapshot.version == version,
            )
            .order_by(ScoreSnapshot.as_of_date.desc(), ScoreSnapshot.id.desc())
            .first()
        )
        if row is None:
            return {"status": "not_computed"}
        return {
            "status": "available",
            "as_of_date": row.as_of_date.isoformat(),
            "horizon": row.horizon,
            "version": row.version,
            "score": row.total,
            "confidence": row.confidence,
            "completeness": row.completeness,
            "components": row.component,
            "config_hash": row.config_hash,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }


    @classmethod
    def _market_reaction(
        cls,
        db: Session,
        security_id: int | None,
        event_time: datetime,
        as_of: datetime,
    ) -> dict[str, Any]:
        if security_id is None:
            return {"status": "not_available"}

        kst = timezone(timedelta(hours=9))
        event_kst = event_time.astimezone(kst) if event_time.tzinfo else event_time.replace(tzinfo=timezone.utc).astimezone(kst)
        as_of_kst = as_of.astimezone(kst) if as_of.tzinfo else as_of.replace(tzinfo=timezone.utc).astimezone(kst)

        # 한국 거래소 정규장 마감 15:30 KST 기준
        # 15:30 이후 공개된 경우 당일 장 마감 후이므로 다음 거래일이 첫 반응일
        post_market = event_kst.time() >= time(15, 30)

        if post_market:
            base_date_target = event_kst.date()
            reaction_date_min = event_kst.date() + timedelta(days=1)
            reaction_timing = "next_trading_day"
        else:
            base_date_target = event_kst.date() - timedelta(days=1)
            reaction_date_min = event_kst.date()
            reaction_timing = "same_trading_day"

        base_row = (
            db.query(DailyPrice)
            .filter(DailyPrice.security_id == security_id, DailyPrice.trade_date <= base_date_target)
            .order_by(DailyPrice.trade_date.desc())
            .first()
        )

        reaction_row = (
            db.query(DailyPrice)
            .filter(
                DailyPrice.security_id == security_id,
                DailyPrice.trade_date >= reaction_date_min,
                DailyPrice.trade_date <= as_of_kst.date(),
            )
            .order_by(DailyPrice.trade_date.asc())
            .first()
        )

        if base_row is None or reaction_row is None:
            return {
                "status": "insufficient_market_data",
                "reaction_timing": reaction_timing,
                "base_trade_date": base_row.trade_date.isoformat() if base_row else None,
                "reaction_trade_date": reaction_row.trade_date.isoformat() if reaction_row else None,
                "causality_note": "사건 시점 전후의 가격 또는 거래량 데이터가 충분하지 않아 반응을 산출할 수 없습니다.",
            }

        p_base = float(base_row.adjusted_close or base_row.close) if (base_row.adjusted_close or base_row.close) is not None else None
        p_reaction = float(reaction_row.adjusted_close or reaction_row.close) if (reaction_row.adjusted_close or reaction_row.close) is not None else None

        ret_pct = None
        if p_base is not None and p_reaction is not None and p_base > 0:
            ret_pct = round((p_reaction - p_base) / p_base * 100, 2)

        vol_base = base_row.volume
        vol_reaction = reaction_row.volume
        vol_change_pct = None
        if vol_base is not None and vol_reaction is not None and vol_base > 0:
            vol_change_pct = round((vol_reaction - vol_base) / vol_base * 100, 2)

        return {
            "status": "available",
            "reaction_timing": reaction_timing,
            "base_trade_date": base_row.trade_date.isoformat(),
            "base_price": p_base,
            "base_volume": vol_base,
            "reaction_trade_date": reaction_row.trade_date.isoformat(),
            "reaction_price": p_reaction,
            "reaction_volume": vol_reaction,
            "return_pct": ret_pct,
            "volume_change_pct": vol_change_pct,
            "causality_note": "본 가격·거래량 변동은 사건 시점 전후의 단순 관찰값이며 공급망 연결에 따른 직접적인 수혜 또는 인과관계를 입증하지 않습니다.",
        }
