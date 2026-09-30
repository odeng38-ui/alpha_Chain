import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.security import require_admin


def test_admin_auth_is_optional_only_in_non_production_without_config(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "test")
    monkeypatch.setattr(settings, "ADMIN_API_KEY", "")
    assert require_admin(None) == "development-admin"


def test_admin_auth_rejects_missing_and_invalid_keys(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "ADMIN_API_KEY", "correct-secret")

    with pytest.raises(HTTPException) as missing:
        require_admin(None)
    assert missing.value.status_code == 401

    with pytest.raises(HTTPException) as invalid:
        require_admin("wrong-secret")
    assert invalid.value.status_code == 401
    assert "correct-secret" not in str(invalid.value.detail)


def test_admin_auth_accepts_configured_key(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "ADMIN_API_KEY", "correct-secret")

def test_mutation_endpoint_requires_admin_header_in_production(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "ADMIN_API_KEY", "correct-secret")
    client = TestClient(app)

    unauthorized = client.post("/api/v1/master/sync", json=[])
    assert unauthorized.status_code == 401

    authorized = client.post(
        "/api/v1/master/sync",
        json=[],
        headers={"X-Admin-Key": "correct-secret"},
    )
    assert authorized.status_code == 400
