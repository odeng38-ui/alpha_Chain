"""Operational alert evaluation for batch failures and stale source data."""

from __future__ import annotations

from datetime import date, datetime, time, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.schema import (
    CollectionCheckpoint,
    DailyPrice,
    DartSyncState,
    Filing,
    MacroObservation,
)

FAILURE_STATUSES = {"FAILED", "ERROR"}
FRESHNESS_LIMITS_DAYS = {"daily_prices": 3, "filings": 7, "macro_observations": 45}


def _as_utc(value: date | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(timezone.utc)
    return datetime.combine(value, time.min, tzinfo=timezone.utc)


def build_operational_alerts(db: Session, now: datetime | None = None) -> dict:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    failures = []
    for row in db.query(CollectionCheckpoint).filter(
        func.upper(CollectionCheckpoint.status).in_(FAILURE_STATUSES)
    ).all():
        failures.append({
            "source": "collection_checkpoint",
            "job_name": row.job_name,
            "entity_id": row.security_id,
            "status": row.status,
            "updated_at": row.updated_at,
            "last_error": row.last_error,
        })
    for row in db.query(DartSyncState).filter(
        func.upper(DartSyncState.status).in_(FAILURE_STATUSES)
    ).all():
        failures.append({
            "source": "dart_sync_state",
            "job_name": "dart_sync",
            "entity_id": row.company_id,
            "status": row.status,
            "updated_at": row.updated_at,
            "last_error": row.last_error,
        })

    latest_values = {
        "daily_prices": db.query(func.max(DailyPrice.trade_date)).scalar(),
        "filings": db.query(func.max(Filing.available_at)).scalar(),
        "macro_observations": db.query(func.max(MacroObservation.available_at)).scalar(),
    }
    freshness = []
    for source, value in latest_values.items():
        latest = _as_utc(value)
        age_days = None if latest is None else max(0.0, (now - latest).total_seconds() / 86400)
        limit = FRESHNESS_LIMITS_DAYS[source]
        freshness.append({
            "source": source,
            "latest_at": latest,
            "age_days": None if age_days is None else round(age_days, 2),
            "threshold_days": limit,
            "status": "missing" if latest is None else ("stale" if age_days > limit else "ok"),
        })

    stale_count = sum(item["status"] != "ok" for item in freshness)
    status = "critical" if failures else ("warning" if stale_count else "ok")
    return {
        "status": status,
        "checked_at": now,
        "summary": {"failed_jobs": len(failures), "stale_or_missing_sources": stale_count},
        "job_failures": failures,
        "data_freshness": freshness,
    }
