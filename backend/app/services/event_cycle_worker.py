"""Single-cycle composition of existing DART, watch, scheduler and PG services."""
from datetime import date, datetime, time, timedelta, timezone

from app.adapters.dart_adapter import DartAdapter, DartAuthError, DartRateLimitError
from app.models.schema import Company, Filing
from app.services.event_auto_scheduler import EventAutoScheduler
from app.services.event_postgres_checkpoint import REVIEW_HOLDS, PostgresEventCheckpoint
from app.services.event_staging_service import EventCycleHistory, EventStagingService
from app.services.event_watch_pipeline import EventWatchPipeline


class ApiBudgetExceeded(RuntimeError):
    pass

class BoundedDartAdapter(DartAdapter):
    def __init__(self, api_key, budget=10):
        super().__init__(api_key, retries=1)
        self.budget = budget
        self.calls = 0

    def _request(self, path, params):
        if self.calls >= self.budget:
            raise ApiBudgetExceeded("api_budget_exhausted")
        self.calls += 1
        return super()._request(path, params)


def validate_scope(corp_codes):
    if len(corp_codes) != 10 or len(set(corp_codes)) != 10 or any(len(x) != 8 or not x.isdigit() for x in corp_codes):
        raise ValueError("Exactly the existing 10 distinct DART corp_codes are required")


def resolve_official_correction(db, row, company_id):
    """No title matching: only an explicit source original receipt may be linked.

    list.json normally has no original_rcept_no. In that case corrections remain
    pending review; an existing inferred correction_of is never treated as proof.
    """
    correction = EventWatchPipeline._is_dart_correction(str(row["report_nm"]))
    if not correction:
        return False, None
    original = str(row.get("original_rcept_no") or "").strip()
    current = str(row.get("rcept_no") or "").strip()
    if len(original) != 14 or not original.isdigit() or original == current:
        return True, None
    parent = db.query(Filing.rcept_no).filter(
        Filing.rcept_no == original, Filing.company_id == company_id,
    ).first()
    return True, parent[0] if parent else None


def run_once(db, *, corp_codes, adapter, end_date, start_date=None, cycle_id=None, max_items=5, max_api_calls=10):
    validate_scope(corp_codes)
    if not 1 <= max_items <= 5 or not 1 <= max_api_calls <= 10:
        raise ValueError("Invalid cycle limits")
    if not isinstance(adapter, BoundedDartAdapter) or adapter.budget > max_api_calls:
        raise ValueError("A bounded existing DART adapter is required")
    cp = PostgresEventCheckpoint(db)
    now = datetime.now(timezone.utc)
    cycle_id = cycle_id or f"dart_{int(now.timestamp()) // 1800}"
    if len(cycle_id) > 64:
        raise ValueError("cycle_id must be at most 64 characters")
    try:
        with cp.acquire_distributed_lock(db):
            # A supplied invocation key is immutable and may not be executed twice.
            existing = db.query(EventCycleHistory).filter_by(cycle_id=cycle_id).first()
            if existing:
                return {"status": "duplicate_cycle", "cycle_id": cycle_id}
            cp.load_database()
            company_rows = db.query(Company.id, Company.corp_code, Company.name).filter(Company.corp_code.in_(corp_codes)).all()
            if len(company_rows) != 10:
                raise ValueError("DART scope must map to exactly 10 company rows")
            companies = {x.corp_code: x for x in company_rows}
            if set(companies) != set(corp_codes):
                raise ValueError("Configured DART scope is not mapped exactly in DB")
            before = cp.state.high_water_mark_cursor
            previous = cp.coverage
            scope = list(corp_codes)
            if previous.get("scope") == scope and not previous.get("is_page_complete", True) and previous.get("resume"):
                progress = dict(previous["resume"])
            else:
                # Discovery watermark is independent of processing HWM. Keep one-day overlap.
                prior_end = previous.get("range_end")
                lower = start_date or (date.fromisoformat(prior_end) - timedelta(days=1) if prior_end else date(2026, 11, 4))
                progress = {"company_index": 0, "page": 1, "start": lower.isoformat(), "end": end_date.isoformat()}
            metadata = {"scope": scope, "resume": dict(progress), "is_page_complete": False, "range_start": progress["start"], "range_end": progress["end"], "completed_corp_codes": scope[:progress["company_index"]]}
            hist = EventCycleHistory(cycle_id=cycle_id, provider_id="DART", started_at=now, status="running", dry_run=False, poll_status="running", is_page_complete=False, high_water_mark_before=before, high_water_mark_after=before, metadata_json=metadata, poll_range_start=progress["start"], poll_range_end=progress["end"])
            db.add(hist)
            db.commit()
            discovered = 0
            poll_status = "partial"
            error = None
            batch = None
            try:
                EventStagingService.recover_abandoned_processing(db, corp_codes=scope, excluded_rcept_nos=tuple(REVIEW_HOLDS))
                cp.load_database()
                pipeline = EventWatchPipeline()
                poll_budget = max(1, max_api_calls - min(max_items, max_api_calls - 1))
                while progress["company_index"] < len(scope) and adapter.calls < poll_budget:
                    code = scope[progress["company_index"]]
                    page = progress["page"]
                    payload = adapter._json("list.json", {"corp_code": code, "bgn_de": progress["start"].replace("-", ""), "end_de": progress["end"].replace("-", ""), "page_no": page, "page_count": 100, "sort": "date", "sort_mth": "asc", "last_reprt_at": "N"})
                    rows = payload.get("list") or []
                    total = max(1, int(payload.get("total_page") or 1))
                    if not rows and page < total:
                        raise RuntimeError("empty_incomplete_page")
                    watch = pipeline.process("DART", rows, as_of=datetime.now(timezone.utc), max_items=100)
                    ready = {str(d.get("source_id")): p for p in watch.router_payloads for d in p.get("source_documents", [])}
                    for row in rows:
                        if row.get("corp_code") != code:
                            raise ValueError("source_company_scope_mismatch")
                        rcp = str(row["rcept_no"])
                        if rcp in cp.state.items:
                            continue
                        title = row["report_nm"]
                        out = ready.get(rcp)
                        correction, original = resolve_official_correction(db, row, companies[code].id)
                        # Date-only DART list metadata: conservative end-of-day KST, not an invented publication time.
                        published = datetime.combine(datetime.strptime(row["rcept_dt"], "%Y%m%d").date(), time(23, 59, 59), tzinfo=timezone(timedelta(hours=9))).isoformat()
                        actionable = bool(out) and (not correction or original is not None)
                        cp.register_discovered_filings([{"rcept_no": rcp, "company_id": companies[code].id, "company_name": companies[code].name, "corp_code": code, "title": title, "published_at": published, "event_type": out.get("event_type", "CORPORATE_FILING") if out else "CORPORATE_FILING", "is_actionable": actionable, "is_correction": correction, "document_version": "2" if correction else "1", "status": "pending"}])
                        item = cp.state.items[rcp]
                        item["correction_of"] = original
                        from app.services.event_staging_service import EventStagingItem
                        db.get(EventStagingItem, rcp).correction_of = original
                        if not actionable:
                            item["last_error"] = "review_required: source or correction needs review"
                        cp.save_checkpoint()
                        discovered += 1
                    if page >= total:
                        progress["company_index"] += 1
                        progress["page"] = 1
                    else:
                        progress["page"] += 1
                    metadata = {**metadata, "resume": dict(progress), "completed_corp_codes": scope[:progress["company_index"]], "is_page_complete": progress["company_index"] == len(scope)}
                    hist.metadata_json = dict(metadata)
                    hist.last_polled_page = page
                    hist.total_pages_detected = total
                    hist.items_discovered = discovered
                    hist.api_calls_made = adapter.calls
                    db.commit()  # The page is durable before its resume position advances.
                poll_status = "success" if metadata["is_page_complete"] else "partial"
                hist.poll_status = poll_status
                hist.is_page_complete = metadata["is_page_complete"]
                hist.metadata_json = dict(metadata)
                db.commit()
                cp.load_database()
                targets = [x["rcept_no"] for x in cp.state.items.values() if x["corp_code"] in scope]
                result = EventAutoScheduler(checkpoint_service=cp, default_max_items=max_items, default_max_api_calls=max_api_calls - adapter.calls).run_cycle(db, max_items=max_items, target_rcept_nos=targets, dry_run=False)
                batch = result.batch_report
                if result.status != "completed":
                    raise RuntimeError("scheduler_cycle_failed")
                final_status = "completed" if not batch or batch.failed_count == 0 else "failed"
            except (DartAuthError, DartRateLimitError, ApiBudgetExceeded) as exc:
                db.rollback()
                poll_status = "auth_failed" if isinstance(exc, DartAuthError) else "rate_limited" if isinstance(exc, DartRateLimitError) else "partial"
                error = poll_status
                final_status = "failed"
            except Exception as exc:
                db.rollback()
                error = type(exc).__name__  # No API URLs, credentials, SQL parameters or raw exception messages.
                poll_status = "error"
                final_status = "failed"
            cp.load_database()
            cp.coverage = metadata
            cp._recompute_high_water_mark()
            hist = db.query(EventCycleHistory).filter_by(cycle_id=cycle_id).one()
            hist.finished_at = datetime.now(timezone.utc)
            hist.status = final_status
            hist.poll_status = poll_status
            hist.is_page_complete = metadata["is_page_complete"]
            hist.high_water_mark_after = cp.state.high_water_mark_cursor if metadata["is_page_complete"] else before
            hist.cursor_advanced = hist.high_water_mark_after != before
            hist.items_discovered = discovered
            hist.items_processed = batch.processed_count if batch else 0
            hist.items_failed = batch.failed_count if batch else 0
            hist.items_skipped = batch.skipped_count if batch else 0
            hist.api_calls_made = adapter.calls
            hist.error_message = error
            hist.metadata_json = dict(metadata)
            db.commit()
            return {"cycle_id": cycle_id, "status": final_status, "poll_status": poll_status, "discovered": discovered, "processed": hist.items_processed, "api_calls": adapter.calls, "is_page_complete": metadata["is_page_complete"]}
    except TimeoutError:
        # Append a separate audit only; never touch the lock owner's history/state.
        db.rollback()
        if not db.query(EventCycleHistory).filter_by(cycle_id=cycle_id).first():
            db.add(EventCycleHistory(cycle_id=cycle_id, provider_id="DART", started_at=now, finished_at=datetime.now(timezone.utc), status="locked", dry_run=False, poll_status="not_polled", is_page_complete=False, metadata_json={"scope": list(corp_codes), "reason": "advisory_lock_unavailable"}))
            db.commit()
        return {"cycle_id": cycle_id, "status": "locked"}
