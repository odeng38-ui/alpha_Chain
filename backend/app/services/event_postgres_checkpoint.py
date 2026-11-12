"""PostgreSQL authoritative checkpoint; never loads or writes legacy JSON."""
from contextlib import contextmanager
from datetime import datetime, timezone
from sqlalchemy import select
from app.services.event_checkpoint_service import EventCheckpointService, CheckpointState
from app.services.event_staging_service import EventStagingItem, EventCycleHistory

REVIEW_HOLDS = frozenset({"20261104000325", "20261110000262"})

class PostgresEventCheckpoint(EventCheckpointService):
    def __init__(self, db, *, readonly=False):
        self.db = db
        self.readonly = readonly
        self.state = CheckpointState()
        self._lock_depth = 0
        self._pg_depth = 0
        self.coverage = {}
        self.load_database()

    def load_database(self):
        self.state = CheckpointState()
        columns = [c.name for c in EventStagingItem.__table__.columns]
        staging_rows = self.db.query(EventStagingItem).all()
        for row in staging_rows:
            item = {name: getattr(row, name) for name in columns}
            for name, value in item.items():
                if isinstance(value, datetime):
                    item[name] = value.isoformat()
            self.state.items[row.rcept_no] = item
        completed_at = max((row.processed_at for row in staging_rows if row.status == "processed" and row.processed_at is not None), default=None)
        self.state.last_analysis_completed_at = completed_at.astimezone(timezone.utc).isoformat() if completed_at else None
        latest = self.db.query(EventCycleHistory).filter(EventCycleHistory.dry_run.is_(False), EventCycleHistory.status != "locked").order_by(EventCycleHistory.started_at.desc()).first()
        if latest:
            self.state.high_water_mark_cursor = latest.high_water_mark_after
            boundary = self.state.items.get(latest.high_water_mark_after)
            if boundary:
                self.state.high_water_mark_timestamp = boundary["published_at"]
            self.state.last_auto_cycle_at = latest.started_at.isoformat()
            self.state.auto_cycle_status = latest.status
            self.coverage = dict(latest.metadata_json or {})
            self.state.last_poll_status = latest.poll_status or "idle"
            self.state.last_poll_error = latest.error_message
        success = self.db.query(EventCycleHistory).filter(EventCycleHistory.dry_run.is_(False), EventCycleHistory.poll_status == "success", EventCycleHistory.is_page_complete.is_(True)).order_by(EventCycleHistory.started_at.desc()).first()
        if success:
            self.state.last_poll_succeeded_at = (success.finished_at or success.started_at).isoformat()
            self.last_success_range = {"start": success.poll_range_start, "end": success.poll_range_end}
        else:
            self.last_success_range = None
        self.last_discovered_count = latest.items_discovered if latest else 0
        self._counts()

    def _counts(self):
        items = list(self.state.items.values())
        self.state.total_tracked = len(items)
        for attr, status in (("pending_count", "pending"), ("processed_count", "processed"), ("failed_count", "failed"), ("quarantined_count", "quarantined"), ("skipped_count", "skipped_non_actionable")):
            setattr(self.state, attr, sum(x["status"] == status for x in items))

    def save_checkpoint(self):
        self._counts()
        if self.readonly:
            return
        for rcp, item in self.state.items.items():
            row = self.db.get(EventStagingItem, rcp)
            if row is None:
                values = {c.name: item[c.name] for c in EventStagingItem.__table__.columns if c.name in item}
                for key in ("published_at", "processed_at", "processing_started_at", "next_retry_at", "created_at", "updated_at"):
                    if isinstance(values.get(key), str):
                        values[key] = datetime.fromisoformat(values[key])
                self.db.add(EventStagingItem(**values))
            elif rcp not in REVIEW_HOLDS:
                for key in ("status", "retry_count", "last_error", "processed_at"):
                    value = item.get(key)
                    if key == "processed_at" and isinstance(value, str):
                        value = datetime.fromisoformat(value)
                    setattr(row, key, value)
        self.db.commit()

    def synchronize_with_database(self, db):
        # The DB staging rows win over memory and JSON; never consume review holds.
        self.load_database()
        return {"recovered_count": 0, "recovered_rcept_nos": [], "total_tracked": self.state.total_tracked}

    def get_next_batch_to_process(self, *, max_items=5, target_rcept_nos=None):
        targets = set(target_rcept_nos) if target_rcept_nos is not None else None
        now = datetime.now(timezone.utc)
        eligible = []
        for item in self.state.items.values():
            retry_at = item.get("next_retry_at")
            if retry_at and datetime.fromisoformat(retry_at).astimezone(timezone.utc) > now:
                continue
            if item["rcept_no"] in REVIEW_HOLDS or not item["is_actionable"] or str(item.get("last_error") or "").startswith("review_required"):
                continue
            if targets is not None and item["rcept_no"] not in targets:
                continue
            if item["status"] == "pending" or (item["status"] == "failed" and item["retry_count"] < 3):
                eligible.append(item)
        return sorted(eligible, key=self.processing_order)[:max_items]

    @staticmethod
    def processing_order(item):
        published = item["published_at"]
        if not isinstance(published, datetime):
            published = datetime.fromisoformat(published.replace("Z", "+00:00"))
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        return published.astimezone(timezone.utc), str(item["rcept_no"])

    def _recompute_high_water_mark(self):
        # Discovery coverage and processing progress are independent. Never change
        # the saved scope/page completion here, even when an old item arrives late.
        if self.coverage.get("is_page_complete") is not True:
            return
        previous = self.state.items.get(self.state.high_water_mark_cursor)
        if previous is None and self.state.high_water_mark_cursor:
            if not self.state.high_water_mark_timestamp:
                return  # Unknown prior boundary: preserve it rather than guess.
            previous = {"rcept_no": self.state.high_water_mark_cursor,
                        "published_at": self.state.high_water_mark_timestamp}
        candidate = None
        for item in sorted(self.state.items.values(), key=self.processing_order):
            if item["status"] not in ("processed", "skipped_non_actionable"):
                break
            candidate = item
        if candidate is not None and (previous is None or self.processing_order(candidate) > self.processing_order(previous)):
            self.state.high_water_mark_cursor = candidate["rcept_no"]
            self.state.high_water_mark_timestamp = candidate["published_at"]
        elif previous is not None:
            self.state.high_water_mark_timestamp = previous["published_at"]
        # A late pending/failed item remains in the explicit staging queue; batch
        # selection intentionally never filters by this monotonic processing HWM.

    @contextmanager
    def acquire_lock(self, timeout_sec=5):
        # The enclosing worker owns the same dedicated PostgreSQL session lock.
        if not self._pg_depth:
            raise RuntimeError("PostgreSQL cycle lock required")
        yield

    @contextmanager
    def acquire_distributed_lock(self, db, lock_key=0x414C5048):
        if self._pg_depth:
            self._pg_depth += 1
            try:
                yield
            finally:
                self._pg_depth -= 1
        else:
            if db.get_bind().dialect.name != "postgresql":
                raise RuntimeError("PostgreSQL required for operational worker")
            with super().acquire_distributed_lock(db, lock_key):
                self._pg_depth = 1
                try:
                    yield
                finally:
                    self._pg_depth = 0

    def get_summary(self):
        summary = super().get_summary()
        review = sum(x["status"] == "pending" and (not x["is_actionable"] or x["rcept_no"] in REVIEW_HOLDS) for x in self.state.items.values())
        complete = self.coverage.get("is_page_complete") is True
        summary.update(last_successful_poll_range=self.last_success_range, last_discovered_count=self.last_discovered_count, state_source="postgresql", review_required_count=review, poll_coverage=self.coverage, polling_incomplete=not complete)
        if not complete:
            summary["data_freshness"] = "poll_incomplete"
            summary["processing_lag_detected"] = True
        if self.state.last_poll_succeeded_at:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(self.state.last_poll_succeeded_at)).total_seconds()
            summary["poll_delayed"] = age > 3600
            if age > 3600 and summary["data_freshness"] == "up_to_date":
                summary["data_freshness"] = "stale_poll"
                summary["processing_lag_detected"] = True
        return summary
