"""Only the newly integrated GET boundary; no workers or B3/B4 regression reruns."""
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.v1.api import api_router
from app.db.session import Base, get_db
from app.models.schema import GlobalEvent, GlobalEventEvidence

NOW = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)


@pytest.fixture
def boundary():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = Session(engine, autoflush=False)
    app = FastAPI()
    app.include_router(api_router)
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as client:
        yield db, engine, client
    db.rollback()
    db.close()
    engine.dispose()


def stored_event(db, *, verified=True):
    row = GlobalEvent(external_id="stored-micron-integration-case", source="SEC", origin_country="US",
                      event_kind="CAPEX_EXPANSION", symbol="MU", title="Stored investment plan",
                      occurred_at=datetime(2025, 6, 12, 12, 40, 40),
                      available_at=datetime(2025, 6, 12, 12, 40, 40),
                      collected_at=datetime(2026, 10, 30, 2, 22, 56),
                      verification_status="verified" if verified else "proposed",
                      shock_score=0, raw_hash="isolated-test-only")
    db.add(row)
    db.commit()
    return row.id


def select_only(engine):
    statements = []

    def guard(conn, cursor, statement, parameters, context, executemany):
        assert statement.lstrip().upper().startswith("SELECT"), statement
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", guard)
    return statements, guard


def test_empty_list_has_no_demo_and_no_write(boundary):
    db, engine, client = boundary
    statements, guard = select_only(engine)
    try:
        response = client.get("/api/v1/event-first/events")
        assert response.status_code == 200
        assert response.json() == {"count": 0, "data": []}
        assert client.get("/api/v1/event-first/events?include_demo=true").status_code == 403
        assert statements and not db.new and not db.dirty
    finally:
        event.remove(engine, "before_cursor_execute", guard)


def test_fixture_id_is_not_promoted_even_in_test_environment(boundary):
    _, _, client = boundary
    response = client.get("/api/v1/event-first/events/evt_sec_0000723125_20250612_d8k/analysis",
                          params={"as_of": NOW.isoformat()})
    assert response.status_code == 404
    assert client.post("/api/v1/event-first/route", json={}).status_code == 404


def test_stored_unverified_event_stays_pending_through_real_query_service(boundary):
    db, engine, client = boundary
    pk = stored_event(db, verified=False)
    statements, guard = select_only(engine)
    try:
        response = client.get(f"/api/v1/event-first/events/{pk}/analysis", params={"as_of": NOW.isoformat()})
        assert response.status_code == 200
        data = response.json()
        assert data["candidates"] == [] and data["anchor"] is None
        assert data["processing_status"] == "pending_data"
        assert "event_not_verified" in data["empty_reasons"]
        assert data["source_type"] == "stored" and data["is_demo"] is False
        assert data["analysis_context"]["mode"] == "stored_data"
        assert statements and not db.new and not db.dirty
        artifacts = Path(".core-integration-02-results")
        artifacts.mkdir(exist_ok=True)
        (artifacts / "pending-analysis.json").write_text(json.dumps(data), encoding="utf-8")
    finally:
        event.remove(engine, "before_cursor_execute", guard)


def test_external_id_delegates_to_existing_b4_and_keeps_cutoff(boundary):
    db, _, client = boundary
    pk = stored_event(db)
    result = {"event": {"event_id": pk, "title": "Stored investment plan"}, "anchors": [],
              "candidates": [], "event_evidence": [], "empty_reasons": ["anchor_missing"]}
    with patch("app.services.event_first_stored_data_bridge.EventSupplyChainService.analyze", return_value=result) as delegate:
        response = client.get("/api/v1/event-first/events/stored-micron-integration-case/analysis",
                              params={"as_of": NOW.isoformat(), "score_version": "v1.0"})
        assert response.status_code == 200
        assert delegate.call_args.kwargs["event_id"] == pk
        assert delegate.call_args.kwargs["as_of"] == NOW
        assert response.json()["candidates"] == []


def test_stored_event_without_approved_evidence_never_creates_chain(boundary):
    db, _, client = boundary
    pk = stored_event(db)
    db.add(GlobalEventEvidence(event_id=pk, evidence_type="REGULATORY", source_url="https://www.sec.gov/",
                              excerpt="Unapproved isolated evidence", published_at=datetime(2025, 6, 12),
                              collected_at=datetime(2026, 10, 30), verification_status="proposed",
                              evidence_hash="isolated-proposed-evidence"))
    db.commit()
    response = client.get(f"/api/v1/event-first/events/{pk}/analysis", params={"as_of": NOW.isoformat()})
    assert response.status_code == 200
    assert response.json()["candidates"] == []
    assert "no_verified_official_evidence_available_as_of" in response.json()["empty_reasons"]


def test_naive_cutoff_rejected_and_missing_schema_is_not_empty_success(boundary):
    _, _, client = boundary
    assert client.get("/api/v1/event-first/events/1/analysis", params={"as_of": "2025-06-12T00:00:00"}).status_code == 422
    empty = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    with Session(empty) as db:
        client.app.dependency_overrides[get_db] = lambda: db
        response = client.get("/api/v1/event-first/events")
        assert response.status_code == 503
        assert response.json()["detail"] == "event_first_storage_unavailable"
    empty.dispose()
