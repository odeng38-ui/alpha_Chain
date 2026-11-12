"""Event Staging and Cycle History Persistence Service (EF-IMPACT-12).

Manages relational persistence of the DART event staging lifecycle and cycle audit history:
- event_staging_item: pending, processing, processed, skipped_non_actionable, failed, quarantined
- event_cycle_history: audit log with poll page completeness & cursor advance guard
- Idempotent migration from legacy JSON checkpoints
- Crash recovery of stale 'processing' leases
- Strict prohibition of CASCADE in DDL rollbacks
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import Session
from app.db.session import Base

logger = logging.getLogger(__name__)


class EventStagingItem(Base):
    __tablename__ = "event_staging_item"

    rcept_no = Column(String(20), primary_key=True)
    company_name = Column(String(255), nullable=False)
    corp_code = Column(String(8), nullable=True)
    company_id = Column(Integer, nullable=True)
    title = Column(String(500), nullable=False)
    published_at = Column(DateTime(timezone=True), nullable=False)
    event_type = Column(String(50), nullable=False, default="CORPORATE_FILING", server_default="CORPORATE_FILING")
    priority_score = Column(Integer, nullable=False, default=60, server_default="60")
    is_actionable = Column(Boolean, nullable=False, default=True, server_default="1")
    is_correction = Column(Boolean, nullable=False, default=False, server_default="0")
    document_version = Column(String(10), nullable=False, default="1", server_default="1")
    correction_of = Column(String(20), nullable=True)
    status = Column(String(30), nullable=False, default="pending", server_default="pending")
    processing_started_at = Column(DateTime(timezone=True), nullable=True)
    locked_by_worker = Column(String(64), nullable=True)
    retry_count = Column(Integer, nullable=False, default=0, server_default="0")
    last_error = Column(Text, nullable=True)
    next_retry_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    processed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'processing', 'processed', 'skipped_non_actionable', 'failed', 'quarantined')",
            name="chk_event_staging_status",
        ),
        Index("idx_event_staging_item_status_order", "status", "published_at", "rcept_no"),
        Index("idx_event_staging_item_processing_lease", "status", "processing_started_at"),
        Index("idx_event_staging_item_corp_code", "corp_code", "published_at"),
    )


class EventCycleHistory(Base):
    __tablename__ = "event_cycle_history"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    cycle_id = Column(String(64), unique=True, nullable=False)
    provider_id = Column(String(30), nullable=False, default="DART")
    started_at = Column(DateTime(timezone=True), nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(30), nullable=False)
    dry_run = Column(Boolean, nullable=False, default=False)
    poll_status = Column(String(30), nullable=True)
    poll_range_start = Column(String(30), nullable=True)
    poll_range_end = Column(String(30), nullable=True)
    last_polled_page = Column(Integer, nullable=False, default=1)
    total_pages_detected = Column(Integer, nullable=False, default=1)
    is_page_complete = Column(Boolean, nullable=False, default=False, server_default="0")
    high_water_mark_before = Column(String(20), nullable=True)
    high_water_mark_after = Column(String(20), nullable=True)
    cursor_advanced = Column(Boolean, nullable=False, default=False)
    items_discovered = Column(Integer, nullable=False, default=0)
    items_processed = Column(Integer, nullable=False, default=0)
    items_failed = Column(Integer, nullable=False, default=0)
    items_skipped = Column(Integer, nullable=False, default=0)
    api_calls_made = Column(Integer, nullable=False, default=0)
    error_message = Column(Text, nullable=True)
    metadata_json = Column("metadata", JSON, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'completed', 'locked', 'failed')",
            name="chk_event_cycle_history_status",
        ),
        Index("idx_event_cycle_history_started_at", started_at.desc()),
        Index("idx_event_cycle_history_poll_scope", "poll_status", "is_page_complete"),
    )


# PostgreSQL Production DDL
POSTGRESQL_STAGING_DDL = [
    """CREATE TABLE IF NOT EXISTS event_staging_item (
    rcept_no VARCHAR(20) PRIMARY KEY,
    company_name VARCHAR(255) NOT NULL,
    corp_code VARCHAR(8),
    company_id INTEGER,
    title VARCHAR(500) NOT NULL,
    published_at TIMESTAMPTZ NOT NULL,
    event_type VARCHAR(50) NOT NULL DEFAULT 'CORPORATE_FILING',
    priority_score INTEGER NOT NULL DEFAULT 60,
    is_actionable BOOLEAN NOT NULL DEFAULT TRUE,
    is_correction BOOLEAN NOT NULL DEFAULT FALSE,
    document_version VARCHAR(10) NOT NULL DEFAULT '1',
    correction_of VARCHAR(20),
    status VARCHAR(30) NOT NULL DEFAULT 'pending',
    processing_started_at TIMESTAMPTZ,
    locked_by_worker VARCHAR(64),
    retry_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    next_retry_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    processed_at TIMESTAMPTZ,
    CONSTRAINT chk_event_staging_status
        CHECK (status IN ('pending', 'processing', 'processed', 'skipped_non_actionable', 'failed', 'quarantined'))
);""",
    "CREATE INDEX IF NOT EXISTS idx_event_staging_item_status_order ON event_staging_item (status, published_at, rcept_no);",
    "CREATE INDEX IF NOT EXISTS idx_event_staging_item_processing_lease ON event_staging_item (status, processing_started_at);",
    "CREATE INDEX IF NOT EXISTS idx_event_staging_item_corp_code ON event_staging_item (corp_code, published_at);",
    """CREATE TABLE IF NOT EXISTS event_cycle_history (
    id BIGSERIAL PRIMARY KEY,
    cycle_id VARCHAR(64) UNIQUE NOT NULL,
    provider_id VARCHAR(30) NOT NULL DEFAULT 'DART',
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    status VARCHAR(30) NOT NULL,
    dry_run BOOLEAN NOT NULL DEFAULT FALSE,
    poll_status VARCHAR(30),
    poll_range_start VARCHAR(30),
    poll_range_end VARCHAR(30),
    last_polled_page INTEGER NOT NULL DEFAULT 1,
    total_pages_detected INTEGER NOT NULL DEFAULT 1,
    is_page_complete BOOLEAN NOT NULL DEFAULT FALSE,
    high_water_mark_before VARCHAR(20),
    high_water_mark_after VARCHAR(20),
    cursor_advanced BOOLEAN NOT NULL DEFAULT FALSE,
    items_discovered INTEGER NOT NULL DEFAULT 0,
    items_processed INTEGER NOT NULL DEFAULT 0,
    items_failed INTEGER NOT NULL DEFAULT 0,
    items_skipped INTEGER NOT NULL DEFAULT 0,
    api_calls_made INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    metadata JSONB,
    CONSTRAINT chk_event_cycle_history_status
        CHECK (status IN ('running', 'completed', 'locked', 'failed'))
);""",
    "CREATE INDEX IF NOT EXISTS idx_event_cycle_history_started_at ON event_cycle_history (started_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_event_cycle_history_poll_scope ON event_cycle_history (poll_status, is_page_complete);",
]

# Strict prohibition: NO CASCADE allowed in rollback statements
STAGING_ROLLBACK_DDL = [
    "DROP INDEX IF EXISTS idx_event_staging_item_status_order;",
    "DROP INDEX IF EXISTS idx_event_staging_item_processing_lease;",
    "DROP INDEX IF EXISTS idx_event_staging_item_corp_code;",
    "DROP TABLE IF EXISTS event_staging_item;",
    "DROP INDEX IF EXISTS idx_event_cycle_history_started_at;",
    "DROP INDEX IF EXISTS idx_event_cycle_history_poll_scope;",
    "DROP TABLE IF EXISTS event_cycle_history;",
]


class EventStagingService:
    """Service handling PostgreSQL staging lifecycle, crash recovery, and cycle history."""

    @staticmethod
    def get_ddl() -> list[str]:
        return POSTGRESQL_STAGING_DDL

    @staticmethod
    def get_rollback_ddl() -> list[str]:
        return STAGING_ROLLBACK_DDL

    @staticmethod
    def create_schema(db: Session) -> None:
        """Create staging and history tables using SQLAlchemy metadata."""
        bind = db.get_bind()
        EventStagingItem.__table__.create(bind, checkfirst=True)
        EventCycleHistory.__table__.create(bind, checkfirst=True)

    @staticmethod
    def rollback_schema(db: Session) -> None:
        """Drop tables without CASCADE."""
        bind = db.get_bind()
        EventCycleHistory.__table__.drop(bind, checkfirst=True)
        EventStagingItem.__table__.drop(bind, checkfirst=True)

    @staticmethod
    def _parse_datetime(dt_val: Any) -> datetime:
        if isinstance(dt_val, datetime):
            if dt_val.tzinfo is None:
                return dt_val.replace(tzinfo=timezone.utc)
            return dt_val
        if not dt_val:
            return datetime.now(timezone.utc)
        s = str(dt_val).strip()
        try:
            return datetime.fromisoformat(s)
        except ValueError:
            pass
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                pass
        return datetime.now(timezone.utc)

    @classmethod
    def migrate_json_checkpoint(
        cls,
        db: Session,
        checkpoint_path: Path,
    ) -> dict[str, Any]:
        """Idempotently migrate tracked items from a JSON checkpoint into event_staging_item.

        Also verifies against existing 'filing' table to guarantee status consistency.
        """
        if not checkpoint_path.exists():
            return {"migrated_count": 0, "status": "no_checkpoint_file"}

        data = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        items = data.get("items", {})

        migrated_count = 0
        processed_count = 0
        skipped_count = 0

        # Check existing filings for SSOT reconciliation
        from app.models.schema import Filing

        for rcp_no, item in items.items():
            filing_exists = db.query(Filing.rcept_no).filter(Filing.rcept_no == rcp_no).first() is not None

            status = item.get("status", "pending")
            if filing_exists:
                status = "processed"

            if status == "processed":
                processed_count += 1
            elif status == "skipped_non_actionable":
                skipped_count += 1

            published_dt = cls._parse_datetime(item.get("published_at"))
            processed_dt = cls._parse_datetime(item.get("processed_at")) if status == "processed" else None

            existing_record = db.query(EventStagingItem).filter(EventStagingItem.rcept_no == rcp_no).first()
            if existing_record:
                existing_record.company_name = item.get("company_name", "")
                existing_record.corp_code = item.get("corp_code", "")
                existing_record.company_id = item.get("company_id")
                existing_record.title = item.get("title", "")
                existing_record.published_at = published_dt
                existing_record.event_type = item.get("event_type", "CORPORATE_FILING")
                existing_record.priority_score = int(item.get("priority_score", 60))
                existing_record.is_actionable = bool(item.get("is_actionable", True))
                existing_record.is_correction = bool(item.get("is_correction", False))
                existing_record.document_version = str(item.get("document_version", "1"))
                existing_record.correction_of = item.get("correction_of")
                existing_record.status = status
                existing_record.retry_count = int(item.get("retry_count", 0))
                existing_record.last_error = item.get("last_error")
                existing_record.processed_at = processed_dt
                existing_record.updated_at = datetime.now(timezone.utc)
            else:
                new_row = EventStagingItem(
                    rcept_no=rcp_no,
                    company_name=item.get("company_name", ""),
                    corp_code=item.get("corp_code", ""),
                    company_id=item.get("company_id"),
                    title=item.get("title", ""),
                    published_at=published_dt,
                    event_type=item.get("event_type", "CORPORATE_FILING"),
                    priority_score=int(item.get("priority_score", 60)),
                    is_actionable=bool(item.get("is_actionable", True)),
                    is_correction=bool(item.get("is_correction", False)),
                    document_version=str(item.get("document_version", "1")),
                    correction_of=item.get("correction_of"),
                    status=status,
                    retry_count=int(item.get("retry_count", 0)),
                    last_error=item.get("last_error"),
                    processed_at=processed_dt,
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
                db.add(new_row)

            migrated_count += 1

        # Record initial audit entry for legacy migration
        hwm = data.get("high_water_mark_cursor")
        hist_existing = db.query(EventCycleHistory).filter(EventCycleHistory.cycle_id == "cycle_legacy_migration_ef12").first()
        now_dt = datetime.now(timezone.utc)
        if not hist_existing:
            hist_row = EventCycleHistory(
                cycle_id="cycle_legacy_migration_ef12",
                provider_id="DART",
                started_at=now_dt,
                finished_at=now_dt,
                status="completed",
                dry_run=False,
                poll_status="migrated",
                high_water_mark_before=None,
                high_water_mark_after=hwm,
                cursor_advanced=True,
                items_discovered=migrated_count,
                items_processed=processed_count,
                items_skipped=skipped_count,
                metadata_json={"source": "dart_event_checkpoint.json"},
            )
            db.add(hist_row)

        db.commit()

        return {
            "migrated_count": migrated_count,
            "processed_count": processed_count,
            "skipped_count": skipped_count,
            "high_water_mark": hwm,
            "status": "success",
        }

    @staticmethod
    def recover_abandoned_processing(
        db: Session,
        lease_timeout_minutes: int = 10,
        *,
        corp_codes: Sequence[str] | None = None,
        excluded_rcept_nos: Sequence[str] = (),
    ) -> dict[str, Any]:
        """Recover items left in 'processing' status due to abnormal worker crash.

        1. If rcept_no already reached 'filing' table -> mark 'processed'.
        2. If not reached 'filing' and retry_count < 3 -> reset to 'pending' with retry_count + 1.
        3. If retry_count >= 3 -> quarantine item to prevent toxic infinite retries.
        """
        from app.models.schema import Filing

        cutoff = datetime.now(timezone.utc) - timedelta(minutes=lease_timeout_minutes)
        query = db.query(EventStagingItem).filter(
            EventStagingItem.status == "processing",
            (EventStagingItem.processing_started_at.is_(None) | (EventStagingItem.processing_started_at < cutoff)),
        )
        if corp_codes is not None:
            query = query.filter(EventStagingItem.corp_code.in_(corp_codes))
        if excluded_rcept_nos:
            query = query.filter(EventStagingItem.rcept_no.notin_(excluded_rcept_nos))
        stale_items = query.all()

        recovered_to_processed = []
        reset_to_pending = []
        quarantined = []

        now_dt = datetime.now(timezone.utc)

        for item in stale_items:
            rcp = item.rcept_no
            retries = item.retry_count or 0

            # Check if actually saved in Filing table
            filing_exists = db.query(Filing.rcept_no).filter(Filing.rcept_no == rcp).first() is not None
            if filing_exists:
                item.status = "processed"
                item.processing_started_at = None
                item.locked_by_worker = None
                item.processed_at = now_dt
                item.updated_at = now_dt
                recovered_to_processed.append(rcp)
            elif retries + 1 >= 3:
                item.status = "quarantined"
                item.retry_count = retries + 1
                item.last_error = "Quarantined: crashed 3 times during processing lease"
                item.processing_started_at = None
                item.locked_by_worker = None
                item.updated_at = now_dt
                quarantined.append(rcp)
            else:
                item.status = "pending"
                item.retry_count = retries + 1
                item.last_error = "Worker lease expired; reset to pending"
                item.processing_started_at = None
                item.locked_by_worker = None
                item.updated_at = now_dt
                reset_to_pending.append(rcp)

        db.commit()
        return {
            "total_stale_found": len(stale_items),
            "recovered_to_processed": recovered_to_processed,
            "reset_to_pending": reset_to_pending,
            "quarantined": quarantined,
        }

    @staticmethod
    def claim_batch_for_processing(
        db: Session,
        max_items: int = 5,
        worker_id: str = "worker_default",
        target_rcept_nos: Sequence[str] | None = None,
    ) -> list[str]:
        """Atomically transition eligible pending/failed items into 'processing'."""
        query = db.query(EventStagingItem).filter(
            EventStagingItem.status.in_(["pending", "failed"]),
            EventStagingItem.retry_count < 3,
        )

        if target_rcept_nos:
            query = query.filter(EventStagingItem.rcept_no.in_(target_rcept_nos))

        now_dt = datetime.now(timezone.utc)
        candidates = (
            query.order_by(EventStagingItem.published_at.asc(), EventStagingItem.rcept_no.asc())
            .limit(max_items)
            .all()
        )

        claimed = []
        for item in candidates:
            item.status = "processing"
            item.processing_started_at = now_dt
            item.locked_by_worker = worker_id
            item.updated_at = now_dt
            claimed.append(item.rcept_no)

        db.commit()
        return claimed

    @classmethod
    def record_cycle_history(
        cls,
        db: Session,
        *,
        cycle_id: str,
        provider_id: str = "DART",
        started_at: str | datetime,
        finished_at: str | datetime,
        status: str,
        dry_run: bool = False,
        poll_status: str | None = None,
        poll_range_start: str | None = None,
        poll_range_end: str | None = None,
        last_polled_page: int = 1,
        total_pages_detected: int = 1,
        is_page_complete: bool = True,
        high_water_mark_before: str | None = None,
        high_water_mark_after: str | None = None,
        items_discovered: int = 0,
        items_processed: int = 0,
        items_failed: int = 0,
        items_skipped: int = 0,
        api_calls_made: int = 0,
        error_message: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> bool:
        """Record cycle history and enforce cursor advance constraints.

        STRICT RULE (Condition 4):
        If is_page_complete is False (e.g. stopped early due to API call limit),
        cursor advancement is STRICTLY FORBIDDEN (high_water_mark_after = before, cursor_advanced = False).
        """
        cursor_advanced = False
        effective_hwm_after = high_water_mark_after

        if not is_page_complete:
            effective_hwm_after = high_water_mark_before
            cursor_advanced = False
            logger.warning(
                "Cycle %s page collection was incomplete (page %d/%d). Halting HWM advance.",
                cycle_id, last_polled_page, total_pages_detected
            )
        elif effective_hwm_after and effective_hwm_after != high_water_mark_before:
            cursor_advanced = True

        st_dt = cls._parse_datetime(started_at)
        fn_dt = cls._parse_datetime(finished_at)

        existing = db.query(EventCycleHistory).filter(EventCycleHistory.cycle_id == cycle_id).first()
        if existing:
            existing.finished_at = fn_dt
            existing.status = status
            existing.poll_status = poll_status
            existing.is_page_complete = is_page_complete
            existing.high_water_mark_after = effective_hwm_after
            existing.cursor_advanced = cursor_advanced
            existing.items_processed = items_processed
            existing.items_failed = items_failed
            existing.error_message = error_message
            existing.metadata_json = dict(metadata or {})
        else:
            row = EventCycleHistory(
                cycle_id=cycle_id,
                provider_id=provider_id,
                started_at=st_dt,
                finished_at=fn_dt,
                status=status,
                dry_run=dry_run,
                poll_status=poll_status,
                poll_range_start=poll_range_start,
                poll_range_end=poll_range_end,
                last_polled_page=last_polled_page,
                total_pages_detected=total_pages_detected,
                is_page_complete=is_page_complete,
                high_water_mark_before=high_water_mark_before,
                high_water_mark_after=effective_hwm_after,
                cursor_advanced=cursor_advanced,
                items_discovered=items_discovered,
                items_processed=items_processed,
                items_failed=items_failed,
                items_skipped=items_skipped,
                api_calls_made=api_calls_made,
                error_message=error_message,
                metadata_json=dict(metadata or {}),
            )
            db.add(row)

        db.commit()
        return cursor_advanced
