"""Read-only lifecycle diagnostics for the active security master."""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from datetime import date
from html.parser import HTMLParser
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.models.schema import (
    AuditLog,
    CollectionCheckpoint,
    Company,
    DailyPrice,
    Security,
)

FAILURE_STATUSES = {"FAILED", "ERROR"}


def build_master_lifecycle_report(db: Session, sample_limit: int = 30) -> dict[str, Any]:
    """Classify active common securities without mutating master data."""
    securities = db.query(Security, Company).join(
        Company, Company.id == Security.company_id,
    ).filter(
        Security.security_type == "COMMON",
        Security.effective_to.is_(None),
    ).order_by(Security.id).all()
    priced_ids = {
        row[0] for row in db.query(DailyPrice.security_id).distinct().all()
    }
    checkpoints = {
        row.security_id: row
        for row in db.query(CollectionCheckpoint).filter(
            CollectionCheckpoint.job_name == "daily_price",
        ).all()
    }

    counts: Counter[str] = Counter()
    market_counts: dict[str, Counter[str]] = defaultdict(Counter)
    samples: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for security, company in securities:
        checkpoint = checkpoints.get(security.id)
        ticker_valid = len(security.ticker) == 6 and security.ticker.isdigit()
        failed = (
            checkpoint is not None
            and (checkpoint.status or "").upper() in FAILURE_STATUSES
        )
        no_price_error = (
            failed
            and "no price data found" in (checkpoint.last_error or "").lower()
        )

        if (company.status or "ACTIVE").upper() != "ACTIVE":
            category = "status_mismatch"
        elif security.id in priced_ids:
            category = "priced"
        elif not ticker_valid:
            category = "invalid_ticker"
        elif no_price_error:
            category = "unavailable_candidate"
        elif failed:
            category = "collection_error"
        elif checkpoint is None:
            category = "unattempted"
        else:
            category = "no_price"

        counts[category] += 1
        market_counts[security.market or "UNKNOWN"][category] += 1
        if category != "priced" and len(samples[category]) < sample_limit:
            samples[category].append({
                "security_id": security.id,
                "ticker": security.ticker,
                "company_name": company.name,
                "company_status": company.status,
                "market": security.market,
                "checkpoint_status": checkpoint.status if checkpoint else None,
                "last_success_date": (
                    checkpoint.last_success_date if checkpoint else None
                ),
            })

    total = len(securities)
    priced = counts["priced"]
    unresolved = total - priced
    return {
        "summary": {
            "active_common": total,
            "priced": priced,
            "unresolved": unresolved,
            "coverage_percent": round(priced / total * 100, 2) if total else 100.0,
            "safe_to_auto_close": 0,
        },
        "categories": {
            key: counts[key]
            for key in (
                "priced",
                "unavailable_candidate",
                "status_mismatch",
                "collection_error",
                "unattempted",
                "no_price",
                "invalid_ticker",
            )
        },
        "markets": {
            market: dict(sorted(values.items()))
            for market, values in sorted(market_counts.items())
        },
        "samples": dict(samples),
        "policy": {
            "mode": "read_only",
            "auto_close_enabled": False,
            "closure_requirement": "confirmed absent from a complete KRX listing snapshot",
        },
    }

KIND_CORP_LIST_URL = "https://kind.krx.co.kr/corpgeneral/corpList.do"
KIND_MARKETS = {"유가": "KOSPI", "코스닥": "KOSDAQ"}


class _KindTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None


def parse_kind_listings(content: bytes) -> list[dict[str, str]]:
    parser = _KindTableParser()
    parser.feed(content.decode("euc-kr", errors="replace"))
    records = []
    for row in parser.rows[1:]:
        if len(row) < 6:
            continue
        market = KIND_MARKETS.get(row[1])
        ticker = row[2].strip()
        if market and len(ticker) == 6:
            records.append({
                "name": row[0],
                "market": market,
                "ticker": ticker,
                "listed_at": row[5],
            })
    return records


def normalize_kind_listings(listings: list[dict[str, str]]) -> list[dict[str, str]]:
    by_ticker: dict[str, dict[str, str]] = {}
    for row in listings:
        existing = by_ticker.get(row["ticker"])
        if existing is not None and existing != row:
            raise ValueError(f"conflicting KIND rows for ticker={row['ticker']}")
        by_ticker[row["ticker"]] = row
    if len(by_ticker) < 1000:
        raise ValueError("KIND listing snapshot is incomplete")
    return list(by_ticker.values())


def fetch_kind_listings(timeout: float = 60.0) -> list[dict[str, str]]:
    response = httpx.get(
        KIND_CORP_LIST_URL,
        params={"method": "download", "searchType": "13"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=timeout,
    )
    response.raise_for_status()
    return normalize_kind_listings(parse_kind_listings(response.content))


def compare_master_to_kind_snapshot(
    db: Session,
    listings: list[dict[str, str]],
    *,
    as_of: date | None = None,
    sample_limit: int = 30,
) -> dict[str, Any]:
    as_of = as_of or date.today()
    snapshot = {row["ticker"]: row for row in listings}
    active = db.query(Security, Company).join(
        Company, Company.id == Security.company_id,
    ).filter(
        Security.security_type == "COMMON",
        Security.effective_to.is_(None),
    ).all()
    active_by_ticker = {security.ticker: (security, company) for security, company in active}
    priced_ids = {
        row[0] for row in db.query(DailyPrice.security_id).distinct().all()
    }
    checkpoints = {
        row.security_id: row
        for row in db.query(CollectionCheckpoint).filter(
            CollectionCheckpoint.job_name == "daily_price",
        ).all()
    }

    confirmed_listed = []
    absent = []
    close_candidates = []
    market_mismatches = []
    for ticker, (security, company) in active_by_ticker.items():
        listing = snapshot.get(ticker)
        if listing:
            confirmed_listed.append(ticker)
            if security.market not in {"UNKNOWN", listing["market"]}:
                market_mismatches.append({
                    "security_id": security.id,
                    "ticker": ticker,
                    "stored_market": security.market,
                    "snapshot_market": listing["market"],
                })
            continue

        item = {
            "security_id": security.id,
            "ticker": ticker,
            "company_name": company.name,
            "market": security.market,
        }
        absent.append(item)
        checkpoint = checkpoints.get(security.id)
        no_price_failure = (
            checkpoint is not None
            and (checkpoint.status or "").upper() in FAILURE_STATUSES
            and "no price data found" in (checkpoint.last_error or "").lower()
        )
        if security.id not in priced_ids and no_price_failure:
            close_candidates.append(item)

    new_tickers = sorted(set(snapshot) - set(active_by_ticker))
    payload_hash = hashlib.sha256(
        "\n".join(sorted(snapshot)).encode()
    ).hexdigest()
    return {
        "snapshot": {
            "source": "KRX_KIND",
            "as_of": as_of,
            "total": len(listings),
            "kospi": sum(row["market"] == "KOSPI" for row in listings),
            "kosdaq": sum(row["market"] == "KOSDAQ" for row in listings),
            "sha256": payload_hash,
        },
        "comparison": {
            "active_common": len(active),
            "confirmed_listed": len(confirmed_listed),
            "absent_from_snapshot": len(absent),
            "safe_close_candidates": len(close_candidates),
            "new_snapshot_tickers": len(new_tickers),
            "market_mismatches": len(market_mismatches),
        },
        "samples": {
            "absent_from_snapshot": absent[:sample_limit],
            "safe_close_candidates": close_candidates[:sample_limit],
            "new_snapshot_tickers": [
                snapshot[ticker] for ticker in new_tickers[:sample_limit]
            ],
            "market_mismatches": market_mismatches[:sample_limit],
        },
        "policy": {
            "mode": "preview",
            "mutations_applied": False,
            "close_requires_explicit_apply": True,
        },
    }

def apply_master_lifecycle_snapshot(
    db: Session,
    listings: list[dict[str, str]],
    *,
    expected_sha256: str,
    expected_safe_close_candidates: int,
    expected_confirmed_listed: int,
    max_close: int = 100,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Apply a previously previewed KIND snapshot with strict count/hash guards."""
    as_of = as_of or date.today()
    preview = compare_master_to_kind_snapshot(db, listings, as_of=as_of, sample_limit=0)
    snapshot = preview["snapshot"]
    comparison = preview["comparison"]
    if snapshot["sha256"] != expected_sha256:
        raise ValueError("snapshot hash does not match the confirmed preview")
    if comparison["safe_close_candidates"] != expected_safe_close_candidates:
        raise ValueError("safe-close count changed after preview")
    if comparison["confirmed_listed"] != expected_confirmed_listed:
        raise ValueError("confirmed-listed count changed after preview")

    snapshot_by_ticker = {row["ticker"]: row for row in listings}
    active = db.query(Security).filter(
        Security.security_type == "COMMON",
        Security.effective_to.is_(None),
    ).all()
    priced_ids = {
        row[0] for row in db.query(DailyPrice.security_id).distinct().all()
    }
    checkpoints = {
        row.security_id: row
        for row in db.query(CollectionCheckpoint).filter(
            CollectionCheckpoint.job_name == "daily_price",
        ).all()
    }

    market_updated = 0
    closed_ids: list[int] = []
    affected_company_ids: set[int] = set()
    for security in sorted(active, key=lambda item: item.id):
        listing = snapshot_by_ticker.get(security.ticker)
        if listing is not None:
            if security.market != listing["market"]:
                security.market = listing["market"]
                market_updated += 1
            continue

        checkpoint = checkpoints.get(security.id)
        no_price_failure = (
            checkpoint is not None
            and (checkpoint.status or "").upper() in FAILURE_STATUSES
            and "no price data found" in (checkpoint.last_error or "").lower()
        )
        if security.id in priced_ids or not no_price_failure:
            continue
        if len(closed_ids) >= max_close:
            continue
        security.effective_to = as_of
        security.delisted_at = security.delisted_at or as_of
        for identifier in security.identifier_maps:
            if identifier.effective_to is None:
                identifier.effective_to = as_of
        closed_ids.append(security.id)
        affected_company_ids.add(security.company_id)

    companies_closed = 0
    for company_id in affected_company_ids:
        has_active_security = db.query(Security.id).filter(
            Security.company_id == company_id,
            Security.effective_to.is_(None),
        ).first() is not None
        if not has_active_security:
            company = db.get(Company, company_id)
            if company is not None and company.status != "DELISTED":
                company.status = "DELISTED"
                companies_closed += 1

    expected_batch_size = min(expected_safe_close_candidates, max_close)
    if len(closed_ids) != expected_batch_size:
        db.rollback()
        raise ValueError("applied close batch differs from confirmed preview")

    db.add(AuditLog(
        actor="admin-api",
        action="apply_master_lifecycle",
        resource_type="krx_kind_snapshot",
        resource_id=snapshot["sha256"],
        before_state={
            "active_common": comparison["active_common"],
            "safe_close_candidates": expected_safe_close_candidates,
            "confirmed_listed": expected_confirmed_listed,
        },
        after_state={
            "securities_closed": len(closed_ids),
            "markets_updated": market_updated,
            "companies_closed": companies_closed,
            "remaining_safe_close_candidates": (
                expected_safe_close_candidates - len(closed_ids)
            ),
        },
        request_id=f"master-lifecycle-{snapshot['sha256'][:16]}",
    ))
    db.commit()
    return {
        "snapshot": snapshot,
        "applied": {
            "securities_closed": len(closed_ids),
            "markets_updated": market_updated,
            "companies_closed": companies_closed,
            "remaining_safe_close_candidates": (
                expected_safe_close_candidates - len(closed_ids)
            ),
        },
        "policy": {
            "mode": "applied",
            "deletions": 0,
            "reversible": True,
        },
    }