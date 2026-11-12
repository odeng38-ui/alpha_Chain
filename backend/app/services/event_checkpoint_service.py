"""Event Checkpoint & Safe Cursor Service for Alpha Chain (EF-IMPACT-09/10).

Ensures deterministic, gap-free sequential ingestion of corporate events:
- Deterministic Ordering by (published_at, rcept_no).
- Gap-free Continuous High-Water Mark cursor calculation.
- Separation of success, failure, quarantined, and skipped_non_actionable states.
- Persistent JSON checkpoint with automatic crash recovery from PostgreSQL Filing table.
- Correction version tracking ([기재정정]).
- Bounded batch sizing (e.g. max 3~5 filings per operational cycle).
- File locking mechanism to prevent concurrent execution conflicts.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

DEFAULT_CHECKPOINT_PATH = Path("var/checkpoints/dart_event_checkpoint.json")


@dataclass
class EventProcessingRecord:
    rcept_no: str
    company_name: str
    company_id: int
    corp_code: str
    title: str
    published_at: str
    event_type: str
    priority_score: int
    is_actionable: bool
    is_correction: bool
    document_version: str
    status: str  # 'pending', 'processing', 'processed', 'failed', 'quarantined', 'skipped_non_actionable'
    retry_count: int = 0
    last_error: str | None = None
    processed_at: str | None = None


@dataclass
class CheckpointState:
    provider_id: str = "DART"
    high_water_mark_cursor: str | None = None  # Highest contiguous processed rcept_no
    high_water_mark_timestamp: str | None = None
    last_evaluated_at: str | None = None
    last_auto_cycle_at: str | None = None
    auto_cycle_status: str | None = "idle"
    last_poll_succeeded_at: str | None = None
    last_poll_status: str = "idle"
    last_poll_error: str | None = None
    last_analysis_completed_at: str | None = None
    currently_processing_rcept_no: str | None = None
    total_tracked: int = 0
    pending_count: int = 0
    processed_count: int = 0
    failed_count: int = 0
    quarantined_count: int = 0
    skipped_count: int = 0
    items: dict[str, dict[str, Any]] = field(default_factory=dict)


class EventCheckpointService:
    """Manages ingestion state, cursor progression, and crash recovery."""

    def __init__(self, checkpoint_path: Path | str | None = None) -> None:
        if checkpoint_path is None:
            base_dir = Path(__file__).parents[2]
            self.checkpoint_file = base_dir / "var" / "checkpoints" / "dart_event_checkpoint.json"
        else:
            self.checkpoint_file = Path(checkpoint_path)

        self.lock_file = self.checkpoint_file.with_suffix(".lock")
        self.checkpoint_file.parent.mkdir(parents=True, exist_ok=True)
        self._lock_depth = 0
        self.state = self._load_checkpoint()

    @contextlib.contextmanager
    def acquire_lock(self, timeout_sec: float = 5.0) -> Iterator[None]:
        """Simple cross-platform cooperative reentrant lock file mechanism."""
        if self._lock_depth > 0:
            self._lock_depth += 1
            try:
                yield
            finally:
                self._lock_depth -= 1
            return

        import time

        start = time.time()
        locked = False
        while time.time() - start < timeout_sec:
            try:
                # O_CREAT | O_EXCL ensures atomic creation
                fd = os.open(str(self.lock_file), os.O_CREAT | os.O_EXCL | os.O_RDWR)
                os.write(fd, f"{os.getpid()}".encode("utf-8"))
                os.close(fd)
                locked = True
                break
            except FileExistsError:
                time.sleep(0.1)

        if not locked:
            raise TimeoutError(f"Could not acquire lock for checkpoint within {timeout_sec}s: {self.lock_file}")

        self._lock_depth = 1
        try:
            yield
        finally:
            self._lock_depth = 0
            try:
                if self.lock_file.exists():
                    self.lock_file.unlink()
            except OSError:
                pass

    def _load_checkpoint(self) -> CheckpointState:
        """Load state from disk or initialize empty state."""
        if self.checkpoint_file.exists():
            try:
                data = json.loads(self.checkpoint_file.read_text(encoding="utf-8"))
                return CheckpointState(
                    provider_id=data.get("provider_id", "DART"),
                    high_water_mark_cursor=data.get("high_water_mark_cursor"),
                    high_water_mark_timestamp=data.get("high_water_mark_timestamp"),
                    last_evaluated_at=data.get("last_evaluated_at"),
                    last_auto_cycle_at=data.get("last_auto_cycle_at"),
                    auto_cycle_status=data.get("auto_cycle_status", "idle"),
                    last_poll_succeeded_at=data.get("last_poll_succeeded_at"),
                    last_poll_status=data.get("last_poll_status", "idle"),
                    last_poll_error=data.get("last_poll_error"),
                    last_analysis_completed_at=data.get("last_analysis_completed_at"),
                    currently_processing_rcept_no=data.get("currently_processing_rcept_no"),
                    total_tracked=data.get("total_tracked", 0),
                    pending_count=data.get("pending_count", 0),
                    processed_count=data.get("processed_count", 0),
                    failed_count=data.get("failed_count", 0),
                    quarantined_count=data.get("quarantined_count", 0),
                    skipped_count=data.get("skipped_count", 0),
                    items=data.get("items", {}),
                )
            except Exception as e:
                logger.warning("Could not read checkpoint file %s: %s. Initializing new state.", self.checkpoint_file, e)
        return CheckpointState()

    def save_checkpoint(self) -> None:
        """Persist current state atomically to disk."""
        items = self.state.items
        self.state.total_tracked = len(items)
        self.state.pending_count = sum(1 for it in items.values() if it.get("status") == "pending")
        self.state.processed_count = sum(1 for it in items.values() if it.get("status") == "processed")
        self.state.failed_count = sum(1 for it in items.values() if it.get("status") == "failed")
        self.state.quarantined_count = sum(1 for it in items.values() if it.get("status") == "quarantined")
        self.state.skipped_count = sum(1 for it in items.values() if it.get("status") == "skipped_non_actionable")
        self.state.last_evaluated_at = datetime.now(timezone.utc).isoformat()

        data = asdict(self.state)
        temp_path = self.checkpoint_file.with_suffix(".tmp")
        temp_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        temp_path.replace(self.checkpoint_file)

    def synchronize_with_database(self, db: Session) -> dict[str, Any]:
        """Reconcile checkpoint state against PostgreSQL Filing table (crash recovery)."""
        from app.models.schema import Filing

        recovered_rcept_nos = []
        for rcp_no, item in list(self.state.items.items()):
            if hasattr(db, "query"):
                filing = db.query(Filing.rcept_no).filter(Filing.rcept_no == rcp_no).first()
            else:
                filing = db.execute(text("SELECT rcept_no FROM filing WHERE rcept_no = :rcp"), {"rcp": rcp_no}).first()
            if filing and item.get("status") != "processed":
                item["status"] = "processed"
                item["last_error"] = None
                item["processed_at"] = datetime.now(timezone.utc).isoformat()
                recovered_rcept_nos.append(rcp_no)

        self._recompute_high_water_mark()
        self.save_checkpoint()
        return {
            "recovered_count": len(recovered_rcept_nos),
            "recovered_rcept_nos": recovered_rcept_nos,
            "total_tracked": self.state.total_tracked,
        }

    def register_discovered_filings(
        self,
        filings: Sequence[Mapping[str, Any]],
        *,
        db: Session | None = None,
    ) -> list[str]:
        """Register newly discovered filings. Preserves existing status if already tracked."""
        from app.models.schema import Filing

        new_registered = []
        for f in filings:
            rcp_no = str(f["rcept_no"]).strip()
            if not rcp_no:
                continue

            # Check existing tracking
            if rcp_no not in self.state.items:
                is_in_db = False
                if db is not None:
                    is_in_db = db.query(Filing.rcept_no).filter(Filing.rcept_no == rcp_no).first() is not None

                is_actionable = bool(f.get("is_actionable", True))
                status = f.get("status")
                if not status:
                    if is_in_db:
                        status = "processed"
                    elif "is_actionable" in f and not f["is_actionable"]:
                        status = "skipped_non_actionable"
                    else:
                        status = "pending"

                record = {
                    "rcept_no": rcp_no,
                    "company_name": f.get("company_name", ""),
                    "company_id": f.get("company_id", 0),
                    "corp_code": f.get("corp_code", ""),
                    "title": f.get("title", ""),
                    "published_at": str(f.get("published_at") or ""),
                    "event_type": f.get("event_type", "CORPORATE_FILING"),
                    "priority_score": int(f.get("priority_score", 60)),
                    "is_actionable": is_actionable,
                    "is_correction": bool(f.get("is_correction", False)),
                    "document_version": str(f.get("document_version", "1")),
                    "status": status,
                    "retry_count": 0,
                    "last_error": None,
                    "processed_at": datetime.now(timezone.utc).isoformat() if status == "processed" else None,
                }
                self.state.items[rcp_no] = record
                new_registered.append(rcp_no)

        self._recompute_high_water_mark()
        self.save_checkpoint()
        return new_registered

    def get_next_batch_to_process(
        self,
        *,
        max_items: int = 5,
        target_rcept_nos: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Get next batch of items strictly in (published_at ASC, rcept_no ASC) order.

        Allows restricting to specific target_rcept_nos if specified.
        """
        targets_set = set(target_rcept_nos) if target_rcept_nos is not None else None
        candidates = []

        for item in self.state.items.values():
            rcp = item.get("rcept_no")
            if targets_set is not None and rcp not in targets_set:
                continue

            status = item.get("status")
            if status == "pending" or (status == "failed" and item.get("retry_count", 0) < 3):
                candidates.append(item)

        # Stable sort: published_at ascending, rcept_no ascending
        candidates.sort(key=lambda x: (x.get("published_at", ""), x.get("rcept_no", "")))
        return candidates[:max_items]

    def mark_processed(self, rcept_no: str) -> None:
        """Mark an item as successfully analyzed and persisted."""
        if rcept_no in self.state.items:
            self.state.items[rcept_no]["status"] = "processed"
            self.state.items[rcept_no]["last_error"] = None
            now_iso = datetime.now(timezone.utc).isoformat()
            self.state.items[rcept_no]["processed_at"] = now_iso
            self.state.last_analysis_completed_at = now_iso
            self._recompute_high_water_mark()
            self.save_checkpoint()

    def mark_failed(self, rcept_no: str, error_message: str) -> None:
        """Mark an item as failed, incrementing retry count."""
        if rcept_no in self.state.items:
            it = self.state.items[rcept_no]
            retries = it.get("retry_count", 0) + 1
            it["retry_count"] = retries
            it["last_error"] = error_message
            if retries >= 3:
                it["status"] = "quarantined"  # Isolates poison pills after 3 attempts
            else:
                it["status"] = "failed"
            self._recompute_high_water_mark()
            self.save_checkpoint()

    def mark_skipped_non_actionable(self, rcept_no: str, reason: str = "non_actionable") -> None:
        """Mark an item as discovered but skipped from deep analysis (preserves discovery audit)."""
        if rcept_no in self.state.items:
            it = self.state.items[rcept_no]
            it["status"] = "skipped_non_actionable"
            it["is_actionable"] = False
            it["last_error"] = f"skipped: {reason}"
            it["processed_at"] = datetime.now(timezone.utc).isoformat()
            self._recompute_high_water_mark()
            self.save_checkpoint()

    def _recompute_high_water_mark(self) -> None:
        """Calculate continuous high-water mark cursor.

        Processed and skipped_non_actionable items advance cursor.
        Pending, failed, and quarantined items strictly halt cursor advance.
        """
        sorted_items = sorted(
            self.state.items.values(),
            key=lambda x: (x.get("published_at", ""), x.get("rcept_no", "")),
        )

        hwm_cursor = None
        hwm_time = None

        for item in sorted_items:
            status = item.get("status")
            if status in ("processed", "skipped_non_actionable"):
                hwm_cursor = item.get("rcept_no")
                hwm_time = item.get("published_at")
            else:
                # pending, failed, quarantined stop contiguous high-water mark
                break

        self.state.high_water_mark_cursor = hwm_cursor
        self.state.high_water_mark_timestamp = hwm_time

    @contextlib.contextmanager
    def acquire_distributed_lock(self, db: Session, lock_key: int = 0x414C5048) -> Iterator[None]:
        """Acquire PostgreSQL application-level advisory lock (pg_try_advisory_lock).

        Prevents concurrent workers across multiple processes/containers.
        Holds a dedicated connection for the entire cycle duration to ensure
        session-level lock persistence across per-filing commits/rollbacks.
        Fallbacks to cooperative file lock if dialect is not PostgreSQL (e.g. SQLite tests).
        """
        is_pg = False
        bind = None
        try:
            bind = db.get_bind()
            if bind and bind.dialect.name == "postgresql":
                is_pg = True
        except Exception:
            pass

        if is_pg and bind is not None:
            from sqlalchemy import text
            conn = bind.connect()
            locked = False
            try:
                locked = bool(conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_key}).scalar())
                if not locked:
                    raise TimeoutError(f"Could not acquire PostgreSQL advisory lock {lock_key}; another instance active.")
                yield
            finally:
                if locked:
                    try:
                        conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key})
                    except Exception:
                        conn.invalidate()  # Never return a potentially locked session to the pool.
                conn.close()
        else:
            with self.acquire_lock():
                yield

    def update_poll_status(
        self,
        status: str,
        poll_time: str | None = None,
        error: str | None = None,
    ) -> None:
        """Record the outcome of external DART polling."""
        self.state.last_poll_status = status
        self.state.last_poll_error = error
        if status == "success":
            self.state.last_poll_succeeded_at = poll_time or datetime.now(timezone.utc).isoformat()
        self.save_checkpoint()

    def set_currently_processing(self, rcept_no: str | None) -> None:
        """Track active filing being processed in flight."""
        self.state.currently_processing_rcept_no = rcept_no
        self.save_checkpoint()

    def update_cycle_status(self, status: str, cycle_time: str | None = None) -> None:
        """Update and record the automated cycle execution status."""
        self.state.auto_cycle_status = status
        if cycle_time or status == "running":
            self.state.last_auto_cycle_at = cycle_time or datetime.now(timezone.utc).isoformat()
        self.save_checkpoint()

    def get_summary(self) -> dict[str, Any]:
        """Return operational summary for dashboard and monitoring."""
        # Strict freshness determination:
        # If unpolled or last poll failed, prohibit claiming up_to_date
        if self.state.last_poll_succeeded_at is None or self.state.last_poll_status in ("auth_failed", "rate_limited", "error"):
            data_freshness = "stale_unpolled"
        elif self.state.pending_count > 0:
            data_freshness = "pending_items"
        elif self.state.failed_count > 0:
            data_freshness = "has_failures"
        else:
            data_freshness = "up_to_date"

        return {
            "provider_id": self.state.provider_id,
            "total_tracked": self.state.total_tracked,
            "pending_count": self.state.pending_count,
            "processed_count": self.state.processed_count,
            "failed_count": self.state.failed_count,
            "quarantined_count": self.state.quarantined_count,
            "skipped_count": self.state.skipped_count,
            "high_water_mark_cursor": self.state.high_water_mark_cursor,
            "high_water_mark_timestamp": self.state.high_water_mark_timestamp,
            "last_evaluated_at": self.state.last_evaluated_at,
            "last_auto_cycle_at": self.state.last_auto_cycle_at,
            "auto_cycle_status": self.state.auto_cycle_status or "idle",
            "last_poll_succeeded_at": self.state.last_poll_succeeded_at,
            "last_poll_status": self.state.last_poll_status,
            "last_poll_error": self.state.last_poll_error,
            "last_analysis_completed_at": self.state.last_analysis_completed_at,
            "currently_processing_rcept_no": self.state.currently_processing_rcept_no,
            "data_freshness": data_freshness,
            "processing_lag_detected": (self.state.pending_count > 0 or self.state.failed_count > 0 or data_freshness == "stale_unpolled"),
        }
