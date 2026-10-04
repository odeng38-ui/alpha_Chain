"""Operational alert evaluation for batch failures and stale source data."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.schema import (
    AuditLog,
    BacktestRun,
    CollectionCheckpoint,
    DailyPrice,
    DartSyncState,
    Filing,
    MacroObservation,
)
from app.services.event_impact_service import EventImpactV4Service

FAILURE_STATUSES = {"FAILED", "ERROR"}
FRESHNESS_LIMITS_DAYS = {"daily_prices": 3, "filings": 7, "macro_observations": 45}
AUTOMATION_LIMITS_DAYS = {"news": 2, "prices": 4, "impact_revalidation": 9}


def _as_utc(value: date | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(timezone.utc)
    return datetime.combine(value, time.min, tzinfo=timezone.utc)


def _price_recovery_status(db: Session, now: datetime) -> dict:
    rows = db.query(CollectionCheckpoint).filter(
        CollectionCheckpoint.job_name == "daily_price",
    ).all()
    retry_before = now.replace(tzinfo=None) - timedelta(days=settings.PRICE_FAILURE_RETRY_DAYS)
    healthy = 0
    recoverable = 0
    retry_eligible = 0
    action_required = 0
    pending = 0
    for row in rows:
        status = (row.status or "").upper()
        if status == "SUCCESS":
            healthy += 1
        elif status in FAILURE_STATUSES:
            no_new_data = "no price data found" in (row.last_error or "").lower()
            if row.last_success_date is not None and no_new_data:
                recoverable += 1
                updated_at = row.updated_at or datetime.min
                if updated_at <= retry_before:
                    retry_eligible += 1
            else:
                action_required += 1
        else:
            pending += 1
    total = len(rows)
    return {
        "total_checkpoints": total,
        "healthy": healthy,
        "recoverable_failures": recoverable,
        "retry_eligible": retry_eligible,
        "retry_waiting": max(recoverable - retry_eligible, 0),
        "action_required": action_required,
        "pending": pending,
        "progress_percent": round(healthy / total * 100, 2) if total else 100.0,
        "retry_after_days": settings.PRICE_FAILURE_RETRY_DAYS,
        "status": "action_required" if action_required else (
            "recovering" if recoverable or pending else "healthy"
        ),
    }

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
            "recovery_class": (
                "automatic" if row.job_name == "daily_price"
                and row.last_success_date is not None
                and "no price data found" in (row.last_error or "").lower()
                else "action_required"
            ),
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
            "recovery_class": "action_required",
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

    automation = []
    for job_name in ("news", "prices"):
        row = db.query(AuditLog).filter(
            AuditLog.actor == "vercel-cron",
            AuditLog.action == "completed",
            AuditLog.resource_type == "cron_run",
            AuditLog.resource_id == job_name,
        ).order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).first()
        latest = _as_utc(row.created_at) if row else None
        age_days = None if latest is None else max(0.0, (now - latest).total_seconds() / 86400)
        limit = AUTOMATION_LIMITS_DAYS[job_name]
        automation.append({
            "job_name": job_name,
            "latest_success_at": latest,
            "age_days": None if age_days is None else round(age_days, 2),
            "threshold_days": limit,
            "status": "missing" if latest is None else ("stale" if age_days > limit else "ok"),
        })
    weekly = db.query(BacktestRun).filter(
        BacktestRun.score_version == "impact-v4",
        BacktestRun.name.like("impact-v4-weekly-%"),
    ).order_by(BacktestRun.completed_at.desc(), BacktestRun.id.desc()).first()
    weekly_at = _as_utc(weekly.completed_at or weekly.created_at) if weekly else None
    weekly_age = None if weekly_at is None else max(0.0, (now - weekly_at).total_seconds() / 86400)
    weekly_limit = AUTOMATION_LIMITS_DAYS["impact_revalidation"]
    automation.append({
        "job_name": "impact_revalidation",
        "latest_success_at": weekly_at,
        "age_days": None if weekly_age is None else round(weekly_age, 2),
        "threshold_days": weekly_limit,
        "status": "missing" if weekly_at is None else (
            "stale" if weekly_age > weekly_limit else "ok"
        ),
        "run_id": weekly.id if weekly else None,
    })

    latest_impact = db.query(BacktestRun).filter(
        BacktestRun.score_version == "impact-v4",
    ).order_by(BacktestRun.completed_at.desc(), BacktestRun.id.desc()).first()
    impact_report = latest_impact.report if latest_impact and latest_impact.report else {}
    metadata = impact_report.get("model_metadata") or {}
    holdout = (impact_report.get("temporal_validation") or {}).get("holdout") or {}
    acceptance = (holdout.get("horizon_acceptance") or {}).get("5d") or {}
    approval_checks = {
        "calibration_current": (
            metadata.get("calibration_version") == EventImpactV4Service.calibration_version
        ),
        "minimum_holdout_events": holdout.get("events", 0) >= 10,
        "holdout_5d_passed": acceptance.get("status") == "PASSED",
    }
    if latest_impact is None:
        model_status = "missing"
        approved = False
    else:
        approved = all(approval_checks.values())
        model_status = "approved" if approved else "degraded"
    model_health = {
        "status": model_status,
        "model_version": "impact-v4-5d",
        "approved": approved,
        "latest_run_id": latest_impact.id if latest_impact else None,
        "approval_checks": approval_checks,
        "holdout_acceptance": acceptance,
    }

    price_recovery = _price_recovery_status(db, now)

    stale_count = sum(item["status"] != "ok" for item in freshness)
    automation_issue_count = sum(item["status"] != "ok" for item in automation)
    model_degraded = model_status == "degraded"
    critical_failure_count = sum(
        item["recovery_class"] == "action_required" for item in failures
    )
    status = ("critical" if critical_failure_count or model_degraded else
              "warning" if stale_count or automation_issue_count or model_status == "missing"
              else "ok")
    return {
        "status": status,
        "checked_at": now,
        "summary": {
            "failed_jobs": len(failures),
            "action_required_failures": critical_failure_count,
            "stale_or_missing_sources": stale_count,
            "automation_issues": automation_issue_count,
            "model_degraded": model_degraded,
        },
        "job_failures": failures,
        "price_recovery": price_recovery,
        "data_freshness": freshness,
        "automation": automation,
        "model_health": model_health,
    }
