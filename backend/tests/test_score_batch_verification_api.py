from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db.session import Base, get_db
from app.main import app
from app.models.schema import Company, ScoreSnapshot, Security

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
SessionLocal = sessionmaker(bind=engine)


def override_get_db():
    with SessionLocal() as db:
        yield db


@pytest.fixture(autouse=True)
def database(monkeypatch):
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "ADMIN_API_KEY", "test-admin-key")
    yield
    app.dependency_overrides.clear()


def add_security(db, ticker: str, *, active: bool = True):
    company = Company(name=f"Company {ticker}", status="ACTIVE")
    db.add(company)
    db.flush()
    security = Security(
        company_id=company.id,
        market="KOSPI",
        ticker=ticker,
        security_type="COMMON",
        effective_to=None if active else date(2025, 1, 1),
    )
    db.add(security)
    db.flush()
    return security


def add_score(db, security_id: int):
    db.add(ScoreSnapshot(
        security_id=security_id,
        as_of_date=date(2026, 10, 13),
        horizon="20d",
        version="v1.0",
        component={},
        total=50.0,
        weights={},
        explanations={},
        completeness=1.0,
        config_hash="test",
    ))


def get_verification(client: TestClient, **params):
    query = {
        "as_of_date": "2026-10-13",
        "horizon": "20d",
        "version": "v1.0",
        "target_policy": "active_common",
        **params,
    }
    return client.get(
        "/api/v1/scores/batch/verification",
        params=query,
        headers={"X-Admin-Key": "test-admin-key"},
    )


def test_requires_admin_authentication():
    client = TestClient(app)
    response = client.get(
        "/api/v1/scores/batch/verification",
        params={"as_of_date": "2026-10-13"},
    )
    assert response.status_code == 401


@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({"target_policy": "unknown"}, "target_policy"),
        ({"page": 0}, "page"),
        ({"page_size": 0}, "page_size"),
        ({"page_size": 501}, "page_size"),
        ({"horizon": ""}, "horizon"),
    ],
)
def test_rejects_invalid_input(params, field):
    response = get_verification(TestClient(app), **params)
    assert response.status_code == 422
    assert field in str(response.json()["detail"])


def test_empty_result():
    with SessionLocal() as db:
        add_security(db, "000001")
        db.commit()

    response = get_verification(TestClient(app))
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "EMPTY"
    assert body["expected_count"] == 1
    assert body["scored_count"] == 0
    assert body["missing_count"] == 1
    assert body["missing_security_ids"] == [1]
    assert body["has_more_missing"] is False


def test_missing_security_ids_are_paginated_and_policy_is_applied():
    with SessionLocal() as db:
        active = [add_security(db, f"{index:06d}") for index in range(1, 6)]
        inactive = add_security(db, "999999", active=False)
        add_score(db, active[0].id)
        active_ids = [security.id for security in active]
        inactive_id = inactive.id
        db.commit()

    client = TestClient(app)
    first = get_verification(client, page=1, page_size=2).json()
    second = get_verification(client, page=2, page_size=2).json()
    all_common = get_verification(
        client, page=3, page_size=2, target_policy="all_common"
    ).json()

    assert first["expected_count"] == 5
    assert first["scored_count"] == 1
    assert first["missing_count"] == 4
    assert first["missing_security_ids"] == [active_ids[1], active_ids[2]]
    assert first["has_more_missing"] is True
    assert second["missing_security_ids"] == [active_ids[3], active_ids[4]]
    assert second["has_more_missing"] is False
    assert all_common["expected_count"] == 6
    assert all_common["missing_security_ids"] == [inactive_id]


def test_batch_default_response_contract_is_unchanged():
    with SessionLocal() as db:
        add_security(db, "000001")
        db.commit()

    response = TestClient(app).post(
        "/api/v1/scores/batch",
        json={
            "as_of_date": "2026-10-13",
            "security_ids": [],
            "horizon": "20d",
            "version": "v1.0",
        },
        headers={"X-Admin-Key": "test-admin-key"},
    )

    assert response.status_code == 200
    assert response.json() == {"success": 0, "failed": []}
    with SessionLocal() as db:
        assert db.query(ScoreSnapshot).count() == 0
