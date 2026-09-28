from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1.endpoints.ui import company_detail, dashboard
from app.db.session import Base
from app.main import app
from app.models.schema import Company, DailyPrice, Filing, Security

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Session = sessionmaker(bind=engine)


@pytest.fixture(autouse=True)
def database():
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


def test_dashboard_contract_has_freshness_and_empty_states():
    db = Session()
    result = dashboard(date(2025, 1, 1), db)
    assert result["as_of"] == date(2025, 1, 1)
    assert result["strong_industries"] == []
    assert result["new_events"] == []
    assert set(result["freshness"]) == {"prices", "filings", "scores", "macro"}


def test_company_detail_distinguishes_zero_from_missing():
    db = Session()
    company = Company(name="웹기업", status="ACTIVE", industry_id="IT")
    db.add(company)
    db.flush()
    security = Security(company_id=company.id, ticker="123456", market="KOSPI", security_type="COMMON")
    db.add(security)
    db.flush()
    db.add(DailyPrice(
        security_id=security.id, trade_date=date(2025, 1, 1),
        close=Decimal("0"), volume=0,
    ))
    db.add(Filing(
        rcept_no="20250101000001", company_id=company.id, title="테스트 공시",
        filed_at=datetime(2025, 1, 1), available_at=datetime(2025, 1, 1),
        raw_ref="https://example.test/filing",
    ))
    db.commit()
    result = company_detail(company.id, date(2025, 1, 2), db)
    assert result["prices"][0]["close"] == 0.0
    assert result["score"] is None
    assert result["filings"][0]["source_url"] == "https://example.test/filing"


def test_web_mvp_routes_are_registered():
    paths = app.openapi()["paths"]
    assert "/api/v1/ui/dashboard" in paths
    assert "/api/v1/ui/companies/{company_id}" in paths
