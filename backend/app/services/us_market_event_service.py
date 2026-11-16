import hashlib
import json
import statistics
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict

from sqlalchemy.orm import Session

from app.models.schema import Company, EventCompanyLink, GlobalEvent, IdentifierMap, NewsArticle
from app.services.event_impact_service import SYMBOL_INDUSTRY_EXPOSURE

DEFAULT_US_SYMBOLS = ("^GSPC", "^IXIC", "^DJI", "^SOX")
SYMBOL_NAMES = {
    "^GSPC": "S&P 500",
    "^IXIC": "NASDAQ Composite",
    "^DJI": "Dow Jones Industrial Average",
    "^SOX": "PHLX Semiconductor Index",
}


class USMarketEventService:
    def __init__(self, adapter):
        self.adapter = adapter

    @staticmethod
    def detect(symbol, records, min_return=0.02, min_zscore=2.5, window=20):
        events = []
        returns = []
        for index in range(1, len(records)):
            daily_return = records[index].close / records[index - 1].close - 1.0
            history = returns[max(0, len(returns) - window):]
            zscore = None
            if len(history) >= window:
                deviation = statistics.stdev(history)
                if deviation > 0:
                    zscore = (daily_return - statistics.mean(history)) / deviation
            returns.append(daily_return)
            if abs(daily_return) < min_return and (zscore is None or abs(zscore) < min_zscore):
                continue
            return_strength = abs(daily_return) / min_return
            z_strength = abs(zscore) / min_zscore if zscore is not None else 0.0
            shock_score = min(100.0, 50.0 * max(return_strength, z_strength))
            events.append({
                "symbol": symbol,
                "trade_date": records[index].trade_date,
                "return_1d": daily_return,
                "zscore_20d": zscore,
                "shock_score": shock_score,
                "direction": "POSITIVE" if daily_return > 0 else "NEGATIVE",
                "close": records[index].close,
                "volume": records[index].volume,
            })
        return events

    def sync(self, db: Session, as_of: date | None = None, lookback_days: int = 180,
             symbols=DEFAULT_US_SYMBOLS):
        as_of = as_of or date.today()
        start_date = as_of - timedelta(days=max(60, min(lookback_days, 730)))
        created = updated = 0
        failures = []
        touched_external_ids = []
        for symbol in symbols:
            try:
                records = self.adapter.fetch_daily(symbol, start_date, as_of)
                for detected in self.detect(symbol, records):
                    event_date = detected["trade_date"]
                    external_id = f"yahoo:{symbol}:{event_date.isoformat()}:price_shock"
                    payload = {
                        "close": detected["close"], "volume": detected["volume"],
                        "return_1d": detected["return_1d"], "zscore_20d": detected["zscore_20d"],
                    }
                    raw_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
                    row = db.query(GlobalEvent).filter(GlobalEvent.external_id == external_id).first()
                    values = dict(
                        source="YAHOO_FINANCE", origin_country="US", event_kind="MARKET_SHOCK",
                        symbol=symbol, title=f"{SYMBOL_NAMES.get(symbol, symbol)} {detected['direction'].lower()} shock",
                        summary=f"1-day return {detected['return_1d']:.2%}",
                        direction=detected["direction"], occurred_at=datetime.combine(event_date, datetime.min.time()),
                        available_at=datetime.combine(event_date + timedelta(days=1), datetime.min.time()),
                        return_1d=detected["return_1d"], zscore_20d=detected["zscore_20d"],
                        shock_score=detected["shock_score"], event_metadata=payload, raw_hash=raw_hash,
                    )
                    if row is None:
                        db.add(GlobalEvent(external_id=external_id, **values))
                        touched_external_ids.append(external_id)
                        created += 1
                    elif row.raw_hash != raw_hash:
                        for key, value in values.items():
                            setattr(row, key, value)
                        touched_external_ids.append(external_id)
                        updated += 1
            except Exception as exc:
                failures.append({"symbol": symbol, "error": str(exc)})
        db.commit()
        event_ids = [row[0] for row in db.query(GlobalEvent.id).filter(
            GlobalEvent.external_id.in_(touched_external_ids),
        ).all()] if touched_external_ids else []
        return {"as_of": as_of.isoformat(), "symbols": len(tuple(symbols)), "created": created,
                "updated": updated, "event_ids": event_ids, "failures": failures}


# ---------------------------------------------------------------------------
# USMarketCompanyCandidateService (CORE-US-FLOW-03)
# ---------------------------------------------------------------------------


class USMarketCompanyCandidateService:
    """Connect US market signals -> relevant industries -> US company candidates.

    Differentiates:
    - Official SEC corporate event confirmed companies (b4_routing_eligible=True)
    - Industry exposure candidates (market_exposure_candidate, unverified, b4_routing_eligible=False)
    - News mention candidates without SEC filing (news_mention_candidate, unverified, b4_routing_eligible=False)
    """

    @staticmethod
    def resolve_candidates(
        db: Session,
        *,
        market_event_id: int,
        as_of: datetime,
    ) -> Dict[str, Any]:
        market_event = db.get(GlobalEvent, market_event_id)
        if market_event is None:
            raise LookupError(f"GlobalEvent(id={market_event_id}) not found")

        # 1. Market signal source & timestamp
        symbol = market_event.symbol or ""
        exposure_map = SYMBOL_INDUSTRY_EXPOSURE.get(symbol, {})

        # 2. Query all registered US companies
        us_companies = db.query(Company).filter(
            Company.country_code == "US",
        ).order_by(Company.id).all()

        as_of_dt = as_of if isinstance(as_of, datetime) else datetime.combine(as_of, datetime.max.time())
        if as_of_dt.tzinfo is None:
            as_of_dt = as_of_dt.replace(tzinfo=timezone.utc)

        candidates = []
        for comp in us_companies:
            # Check CIK mapping
            cik_map = (
                db.query(IdentifierMap)
                .filter(
                    IdentifierMap.company_id == comp.id,
                    IdentifierMap.source == "CIK",
                    IdentifierMap.effective_to.is_(None),
                )
                .first()
            )
            cik_value = cik_map.source_id_value if cik_map else None

            # Check if there is an official SEC event with an ANCHOR link for this company
            sec_link = (
                db.query(EventCompanyLink, GlobalEvent)
                .join(GlobalEvent, EventCompanyLink.event_id == GlobalEvent.id)
                .filter(
                    EventCompanyLink.company_id == comp.id,
                    EventCompanyLink.role == "ANCHOR",
                    GlobalEvent.source == "SEC",
                    GlobalEvent.occurred_at <= as_of_dt.replace(tzinfo=None),
                )
                .order_by(GlobalEvent.occurred_at.desc())
                .first()
            )

            # Check if news mentions this company
            news_mention = None
            if not sec_link:
                cutoff_news = as_of_dt.replace(tzinfo=None) - timedelta(days=7)
                news_mention = (
                    db.query(NewsArticle)
                    .filter(
                        NewsArticle.published_at <= as_of_dt.replace(tzinfo=None),
                        NewsArticle.published_at >= cutoff_news,
                        NewsArticle.title.ilike(f"%{comp.name}%"),
                    )
                    .order_by(NewsArticle.published_at.desc())
                    .first()
                )

            # Categorize candidate
            if sec_link:
                link_obj, sec_ev = sec_link
                is_verified = (sec_ev.verification_status == "verified" and link_obj.verification_status == "verified")
                candidates.append({
                    "company_id": comp.id,
                    "company_name": comp.name,
                    "legal_name": comp.legal_name,
                    "country_code": comp.country_code,
                    "industry_id": comp.industry_id,
                    "cik": cik_value,
                    "candidate_category": "sec_event_candidate",
                    "b4_routing_eligible": is_verified,
                    "verification_status": "verified" if is_verified else "proposed",
                    "sec_event_id": sec_ev.id,
                    "sec_external_id": sec_ev.external_id,
                    "sec_title": sec_ev.title,
                    "sec_anchor_link_id": link_obj.id,
                    "rationale": f"Official SEC filing confirmed ({sec_ev.title})",
                    "evidence_type": "OFFICIAL_REGULATORY",
                })
            elif comp.industry_id and comp.industry_id in exposure_map:
                weight = exposure_map[comp.industry_id]
                candidates.append({
                    "company_id": comp.id,
                    "company_name": comp.name,
                    "legal_name": comp.legal_name,
                    "country_code": comp.country_code,
                    "industry_id": comp.industry_id,
                    "cik": cik_value,
                    "candidate_category": "market_exposure_candidate",
                    "b4_routing_eligible": False,
                    "verification_status": "unverified",
                    "sec_event_id": None,
                    "sec_external_id": None,
                    "sec_title": None,
                    "sec_anchor_link_id": None,
                    "exposure_weight": weight,
                    "rationale": f"Industry exposure to {comp.industry_id} from {symbol} shock (weight: {weight:.2f})",
                    "b4_ineligibility_reason": "no_official_sec_event_unverified_industry_exposure",
                    "evidence_type": "MARKET_INDEX_EXPOSURE",
                })
            elif news_mention:
                candidates.append({
                    "company_id": comp.id,
                    "company_name": comp.name,
                    "legal_name": comp.legal_name,
                    "country_code": comp.country_code,
                    "industry_id": comp.industry_id,
                    "cik": cik_value,
                    "candidate_category": "news_mention_candidate",
                    "b4_routing_eligible": False,
                    "verification_status": "unverified",
                    "sec_event_id": None,
                    "sec_external_id": None,
                    "sec_title": None,
                    "sec_anchor_link_id": None,
                    "news_article_id": news_mention.id,
                    "rationale": f"Mentioned in global market news ({news_mention.title})",
                    "b4_ineligibility_reason": "news_mention_only_no_official_sec_filing",
                    "evidence_type": "NEWS_MENTION",
                })

        return {
            "market_signal": {
                "event_id": market_event.id,
                "source": market_event.source,
                "symbol": market_event.symbol,
                "occurred_at": market_event.occurred_at.isoformat() if market_event.occurred_at else None,
                "available_at": market_event.available_at.isoformat() if market_event.available_at else None,
                "shock_score": market_event.shock_score,
                "direction": market_event.direction,
                "return_1d": market_event.return_1d,
            },
            "as_of": as_of_dt.isoformat(),
            "identified_industries": exposure_map,
            "us_company_candidates": candidates,
            "b4_forwardable_candidates": [c for c in candidates if c["b4_routing_eligible"]],
            "market_exposure_candidates": [c for c in candidates if c["candidate_category"] == "market_exposure_candidate"],
            "news_mention_candidates": [c for c in candidates if c["candidate_category"] == "news_mention_candidate"],
        }
