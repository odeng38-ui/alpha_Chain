"""Event Auto Scheduler / Auto Runner for Alpha Chain (EF-IMPACT-11).

Provides a bounded, fault-isolated, single-shot cycle executor for scheduled
or on-demand corporate event discovery, watch pipeline orchestration, analysis,
and database ingestion.

Guarantees & Constraints:
- Single-Shot Execution: Each invocation executes exactly one bounded cycle; no unapproved infinite loops or unmonitored background threads.
- Cooperative Reentrant Lock: Ensures only one auto runner process runs at any given moment.
- Database Reconciliation: Restores state from PostgreSQL Filing table on startup (supports serverless / container stateless environments).
- Bounded Batch Size: Respects max_items (default <= 5) per execution.
- Rate Limiting: Respects max_api_calls limit.
- Isolation: Independent per-filing transactions; failures in one filing do not block others.
- Dry-Run Support: Supports safe testing/replay without writing to the database.
- Monitoring & Status: Updates operational_status, cycle time, and data freshness for Event Inbox.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from sqlalchemy.orm import Session

from app.services.event_batch_runner import BatchExecutionReport, EventBatchRunner
from app.services.event_checkpoint_service import EventCheckpointService
from app.services.event_watch_pipeline import EventWatchPipeline, EventWatchResult
from app.adapters.dart_adapter import DartAdapter, DartAuthError, DartRateLimitError

logger = logging.getLogger(__name__)


@dataclass
class AutoCycleResult:
    cycle_id: str
    started_at: str
    finished_at: str
    status: str  # 'completed', 'failed', 'locked', 'skipped'
    dry_run: bool
    batch_report: BatchExecutionReport | None = None
    watch_result: EventWatchResult | None = None
    operational_summary: dict[str, Any] = field(default_factory=dict)
    error_message: str | None = None


class EventAutoScheduler:
    """Bounded, safe runner for automated event processing cycles."""

    def __init__(
        self,
        checkpoint_service: EventCheckpointService | None = None,
        batch_runner: EventBatchRunner | None = None,
        watch_pipeline: EventWatchPipeline | None = None,
        *,
        default_max_items: int = 5,
        default_max_api_calls: int = 10,
    ) -> None:
        self.checkpoint_service = checkpoint_service or EventCheckpointService()
        self.batch_runner = batch_runner or EventBatchRunner(
            checkpoint_service=self.checkpoint_service,
            max_api_calls=default_max_api_calls,
        )
        self.watch_pipeline = watch_pipeline or EventWatchPipeline()
        self.default_max_items = default_max_items
        self.default_max_api_calls = default_max_api_calls

    def run_cycle(
        self,
        db: Session,
        *,
        new_items: Sequence[Any] | None = None,
        provider_id: str = "DART",
        max_items: int | None = None,
        target_rcept_nos: Sequence[str] | None = None,
        dry_run: bool = False,
        lock_timeout_sec: float = 5.0,
    ) -> AutoCycleResult:
        """Run a single bounded execution cycle.

        Steps:
        1. Acquire cooperative lock.
        2. Synchronize checkpoint with DB.
        3. (Optional) Ingest new items through EventWatchPipeline and register in checkpoint.
        4. Execute bounded batch via EventBatchRunner.
        5. Update checkpoint cycle status and return AutoCycleResult.
        """
        now_dt = datetime.now(timezone.utc)
        cycle_id = f"cycle_{now_dt.strftime('%Y%m%d_%H%M%S_%f')}"
        started_at = now_dt.isoformat()
        items_limit = max_items or self.default_max_items

        logger.info(
            "Starting auto cycle %s (dry_run=%s, max_items=%d, provider=%s)",
            cycle_id,
            dry_run,
            items_limit,
            provider_id,
        )

        try:
            with self.checkpoint_service.acquire_distributed_lock(db):
                self.checkpoint_service.update_cycle_status("running", started_at)

                # 1. Sync with DB
                sync_res = self.checkpoint_service.synchronize_with_database(db)
                logger.info("DB sync completed: %s", sync_res)

                # 2. Ingest any new external items via watch pipeline if provided
                watch_result = None
                if new_items:
                    as_of = datetime.now(timezone.utc)
                    watch_result = self.watch_pipeline.process(
                        provider_id=provider_id,
                        items=new_items,
                        as_of=as_of,
                        cursor=self.checkpoint_service.state.high_water_mark_cursor,
                    )
                    # Register new candidates in checkpoint
                    candidate_dicts = []
                    for p in watch_result.router_payloads:
                        source_docs = p.get("source_documents", [])
                        first_doc = source_docs[0] if source_docs else {}
                        rcept_no = first_doc.get("source_id") or p.get("event_candidate_id")
                        company_info = p.get("anchor_company") or {}
                        corp_code = company_info.get("value") if company_info.get("source") == "corp_code" else ""
                        pub_at = first_doc.get("published_at") or p.get("available_at") or started_at
                        if isinstance(pub_at, datetime):
                            pub_at = pub_at.isoformat()

                        candidate_dicts.append({
                            "rcept_no": rcept_no,
                            "company_name": company_info.get("legal_name") or "",
                            "company_id": None,
                            "corp_code": corp_code,
                            "title": first_doc.get("title", ""),
                            "published_at": pub_at,
                            "event_type": p.get("event_type", "CORPORATE_FILING"),
                            "priority_score": p.get("priority_score", 70),
                            "is_actionable": True,
                            "is_correction": False,
                            "document_version": first_doc.get("document_version", "1"),
                        })

                    if candidate_dicts:
                        self.checkpoint_service.register_discovered_filings(candidate_dicts)

                # 3. Execute bounded batch
                batch_report = self.batch_runner.run_batch(
                    db=db,
                    max_items=items_limit,
                    target_rcept_nos=target_rcept_nos,
                    dry_run=dry_run,
                )

                self.checkpoint_service.update_cycle_status("completed")
                finished_at = datetime.now(timezone.utc).isoformat()
                summary = self.checkpoint_service.get_summary()

                return AutoCycleResult(
                    cycle_id=cycle_id,
                    started_at=started_at,
                    finished_at=finished_at,
                    status="completed",
                    dry_run=dry_run,
                    batch_report=batch_report,
                    watch_result=watch_result,
                    operational_summary=summary,
                )

        except TimeoutError as te:
            logger.warning("Auto cycle %s could not acquire lock: %s", cycle_id, te)
            finished_at = datetime.now(timezone.utc).isoformat()
            return AutoCycleResult(
                cycle_id=cycle_id,
                started_at=started_at,
                finished_at=finished_at,
                status="locked",
                dry_run=dry_run,
                error_message=str(te),
                operational_summary=self.checkpoint_service.get_summary(),
            )
        except Exception as ex:
            logger.exception("Auto cycle %s failed with exception: %s", cycle_id, ex)
            try:
                self.checkpoint_service.update_cycle_status("failed")
            except Exception:
                pass
            finished_at = datetime.now(timezone.utc).isoformat()
            return AutoCycleResult(
                cycle_id=cycle_id,
                started_at=started_at,
                finished_at=finished_at,
                status="failed",
                dry_run=dry_run,
                error_message=str(ex),
                operational_summary=self.checkpoint_service.get_summary(),
            )
