import logging
import threading
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func

from app.db.session import SessionLocal
from app.models.schema import CollectionCheckpoint, Security
from app.services import price_service

logger = logging.getLogger(__name__)
_lock = threading.Lock()
_stop_event = threading.Event()
_thread: threading.Thread | None = None
_state: dict[str, Any] = {
    "status": "IDLE",
    "batch_size": 20,
    "processed": 0,
    "inserted": 0,
    "errors": 0,
    "started_at": None,
    "finished_at": None,
    "message": None,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def collection_progress(db) -> dict[str, Any]:
    active_ids = [row[0] for row in db.query(Security.id).filter(
        Security.security_type == "COMMON",
        Security.effective_to.is_(None),
    ).order_by(Security.id).all()]
    rows = db.query(CollectionCheckpoint.status, func.count(CollectionCheckpoint.security_id)).filter(
        CollectionCheckpoint.job_name == "daily_price",
    ).group_by(CollectionCheckpoint.status).all()
    counts = {status.lower(): count for status, count in rows}
    attempted_ids = {row[0] for row in db.query(CollectionCheckpoint.security_id).filter(
        CollectionCheckpoint.job_name == "daily_price",
        CollectionCheckpoint.status.in_(("SUCCESS", "FAILED")),
    ).all()}
    next_offset = next(
        (index for index, security_id in enumerate(active_ids) if security_id not in attempted_ids),
        len(active_ids),
    )
    return {
        "total": len(active_ids),
        "success": counts.get("success", 0),
        "failed": counts.get("failed", 0),
        "pending": max(len(active_ids) - sum(counts.values()), 0) + counts.get("pending", 0),
        "next_offset": next_offset,
    }


def _set_state(**values: Any) -> None:
    with _lock:
        _state.update(values)


def _run(batch_size: int) -> None:
    try:
        while not _stop_event.is_set():
            with SessionLocal() as db:
                progress = collection_progress(db)
                if progress["pending"] <= 0:
                    final_status = "COMPLETED_WITH_ERRORS" if progress["failed"] else "COMPLETED"
                    _set_state(status=final_status, finished_at=_now(), message="All pending securities processed")
                    return
                batch = price_service.incremental_batch_update(
                    db,
                    offset=progress["next_offset"],
                    batch_size=batch_size,
                ).to_dict()
            _set_state(
                processed=_state["processed"] + batch["processed"],
                inserted=_state["inserted"] + batch["total_inserted"],
                errors=_state["errors"] + len(batch["errors"]),
                message=f"Processed offset {batch['offset']} ({batch['processed']} securities)",
            )
        _set_state(status="PAUSED", finished_at=_now(), message="Stopped by operator")
    except Exception as exc:
        logger.exception("Background price collection failed")
        _set_state(status="FAILED", finished_at=_now(), message=str(exc))


def start_collection(batch_size: int = 20) -> dict[str, Any]:
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return {**_state, "started": False}
        _stop_event.clear()
        _state.update({
            "status": "RUNNING",
            "batch_size": batch_size,
            "processed": 0,
            "inserted": 0,
            "errors": 0,
            "started_at": _now(),
            "finished_at": None,
            "message": "Background collection started",
        })
        _thread = threading.Thread(
            target=_run,
            args=(batch_size,),
            name="price-collection-runner",
            daemon=True,
        )
        _thread.start()
        return {**_state, "started": True}


def stop_collection() -> dict[str, Any]:
    with _lock:
        running = _thread is not None and _thread.is_alive()
        if running:
            _state["status"] = "STOPPING"
            _state["message"] = "Stop requested; waiting for current batch"
            _stop_event.set()
        return {**_state, "stop_requested": running}


def runner_status() -> dict[str, Any]:
    with _lock:
        snapshot = dict(_state)
        snapshot["thread_alive"] = _thread is not None and _thread.is_alive()
    with SessionLocal() as db:
        snapshot["collection"] = collection_progress(db)
    return snapshot