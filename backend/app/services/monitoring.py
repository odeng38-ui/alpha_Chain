"""Operational alert evaluation for batch failures and stale source data."""

from __future__ import annotations

from datetime import date, datetime, time, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

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

    stale_count = sum(item["status"] != "ok" for item in freshness)
    automation_issue_count = sum(item["status"] != "ok" for item in automation)
    model_degraded = model_status == "degraded"
    status = ("critical" if failures or model_degraded else
              "warning" if stale_count or automation_issue_count or model_status == "missing"
              else "ok")
    return {
        "status": status,
        "checked_at": now,
        "summary": {
            "failed_jobs": len(failures),
            "stale_or_missing_sources": stale_count,
            "automation_issues": automation_issue_count,
            "model_degraded": model_degraded,
        },
        "job_failures": failures,
        "data_freshness": freshness,
        "automation": automation,
        "model_health": model_health,
    }
