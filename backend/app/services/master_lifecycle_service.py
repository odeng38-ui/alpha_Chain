"""Read-only lifecycle diagnostics for the active security master."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from sqlalchemy.orm import Session

from app.models.schema import CollectionCheckpoint, Company, DailyPrice, Security

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

        if not ticker_valid:
            category = "invalid_ticker"
        elif (company.status or "ACTIVE").upper() != "ACTIVE":
            category = "status_mismatch"
        elif security.id in priced_ids:
            category = "priced"
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