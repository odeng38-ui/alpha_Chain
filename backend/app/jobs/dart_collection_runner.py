import logging
import threading
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from app.adapters.dart_adapter import DartAdapter
from app.config import settings
from app.db.session import SessionLocal
from app.models.schema import Company, DartSyncState, Security
from app.services.dart_service import DartCollectionService
from app.services.industry_service import sync_company_industry

logger = logging.getLogger(__name__)
_lock = threading.Lock()
_stop_event = threading.Event()
_thread: threading.Thread | None = None
_state: dict[str, Any] = {
    "status": "IDLE",
    "batch_size": 2,
    "processed": 0,
    "filings": 0,
    "financial_facts": 0,
    "industries": 0,
    "errors": 0,
    "started_at": None,
    "finished_at": None,
    "message": None,
    "start_date": None,
    "end_date": None,
    "financial_years": [],
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _eligible_companies(db):
    return db.query(Company).join(Security, Security.company_id == Company.id).filter(
        Company.corp_code.isnot(None),
        Company.status == "ACTIVE",
        Security.security_type == "COMMON",
        Security.effective_to.is_(None),
    ).distinct().order_by(Company.id)


def collection_progress(db) -> dict[str, int]:
    company_ids = [row.id for row in _eligible_companies(db).all()]
    states = {
        row.company_id: row.status
        for row in db.query(DartSyncState).filter(DartSyncState.company_id.in_(company_ids)).all()
    } if company_ids else {}
    success = sum(states.get(company_id) == "SUCCESS_FULL" for company_id in company_ids)
    failed = sum(states.get(company_id) == "FAILED" for company_id in company_ids)
    return {
        "total": len(company_ids),
        "success": success,
        "failed": failed,
        "pending": max(len(company_ids) - success - failed, 0),
    }


def _set_state(**values: Any) -> None:
    with _lock:
        _state.update(values)


def _candidate_ids(db, retry_failed: bool, limit: int) -> list[int]:
    companies = _eligible_companies(db).all()
    states = {
        row.company_id: row.status
        for row in db.query(DartSyncState).filter(
            DartSyncState.company_id.in_([company.id for company in companies])
        ).all()
    } if companies else {}
    if retry_failed:
        return [company.id for company in companies if states.get(company.id) == "FAILED"][:limit]
    return [
        company.id for company in companies
        if states.get(company.id) not in ("SUCCESS_FULL", "FAILED")
    ][:limit]


def _sync_one(company_id: int, start_date: date, end_date: date, financial_years: Iterable[str]) -> dict[str, int]:
    with SessionLocal() as db:
        company = db.get(Company, company_id)
        state = db.get(DartSyncState, company_id) or DartSyncState(company_id=company_id)
        db.add(state)
        state.status = "RUNNING"
        state.last_error = None
        db.commit()
        service = DartCollectionService(DartAdapter(settings.DART_API_KEY), settings.DART_RAW_DIR)
        filings = financial_facts = 0
        try:
            filing_start = max(start_date, state.last_filing_date + timedelta(days=1)) if state.last_filing_date else start_date
            if filing_start <= end_date:
                result = service.sync_company(db, company, filing_start, end_date, False)
                filings = result["filings"]
            sync_company_industry(db, service.adapter, company)
            for year in financial_years:
                financial_facts += service.sync_financials(db, company, year, "11011")
            state = db.get(DartSyncState, company_id) or DartSyncState(company_id=company_id)
            db.add(state)
            state.last_filing_date = end_date
            state.status = "SUCCESS_FULL"
            state.last_error = None
            db.commit()
            return {"filings": filings, "financial_facts": financial_facts, "industries": 1, "errors": 0}
        except Exception as exc:
            db.rollback()
            state = db.get(DartSyncState, company_id) or DartSyncState(company_id=company_id)
            db.add(state)
            state.status = "FAILED"
            state.last_error = str(exc)
            db.commit()
            logger.warning("DART batch failed company_id=%s: %s", company_id, exc)
            return {"filings": filings, "financial_facts": financial_facts, "industries": 0, "errors": 1}


def _run(batch_size: int, start_date: date, end_date: date, financial_years: list[str], retry_failed: bool) -> None:
    try:
        while not _stop_event.is_set():
            with SessionLocal() as db:
                company_ids = _candidate_ids(db, retry_failed, batch_size)
            if not company_ids:
                with SessionLocal() as db:
                    progress = collection_progress(db)
                final = "COMPLETED_WITH_ERRORS" if progress["failed"] else "COMPLETED"
                _set_state(status=final, finished_at=_now(), message="DART batch completed")
                return
            for company_id in company_ids:
                if _stop_event.is_set():
                    _set_state(status="PAUSED", finished_at=_now(), message="Stopped by operator")
                    return
                result = _sync_one(company_id, start_date, end_date, financial_years)
                with _lock:
                    _state["processed"] += 1
                    _state["filings"] += result["filings"]
                    _state["financial_facts"] += result["financial_facts"]
                    _state["industries"] += result["industries"]
                    _state["errors"] += result["errors"]
                    _state["message"] = f"Processed company {company_id}"
            if retry_failed:
                _set_state(status="COMPLETED", finished_at=_now(), message="Failed DART companies retried")
                return
        _set_state(status="PAUSED", finished_at=_now(), message="Stopped by operator")
    except Exception as exc:
        logger.exception("Background DART collection failed")
        _set_state(status="FAILED", finished_at=_now(), message=str(exc))


def start_collection(batch_size: int, start_date: date, end_date: date, financial_years: list[str], retry_failed: bool = False) -> dict[str, Any]:
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return {**_state, "started": False}
        _stop_event.clear()
        _state.update({
            "status": "RUNNING",
            "batch_size": batch_size,
            "processed": 0,
            "filings": 0,
            "financial_facts": 0,
            "industries": 0,
            "errors": 0,
            "started_at": _now(),
            "finished_at": None,
            "message": "DART background collection started",
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "financial_years": financial_years,
        })
        _thread = threading.Thread(
            target=_run,
            args=(batch_size, start_date, end_date, financial_years, retry_failed),
            name="dart-collection-runner",
            daemon=True,
        )
        _thread.start()
        return {**_state, "started": True}


def stop_collection() -> dict[str, Any]:
    with _lock:
        running = _thread is not None and _thread.is_alive()
        if running:
            _state["status"] = "STOPPING"
            _state["message"] = "Stop requested; waiting for current company"
            _stop_event.set()
        return {**_state, "stop_requested": running}


def runner_status() -> dict[str, Any]:
    with _lock:
        snapshot = dict(_state)
        snapshot["thread_alive"] = _thread is not None and _thread.is_alive()
    with SessionLocal() as db:
        snapshot["collection"] = collection_progress(db)
    return snapshot