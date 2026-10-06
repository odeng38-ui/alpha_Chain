from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.adapters.fred_adapter import FredAdapter
from app.api.v1.endpoints import macro as macro_endpoint
from app.db.session import Base
from app.models.schema import MacroObservation, MacroSeries
from app.services.fred_service import FredCollectionService

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Session = sessionmaker(bind=engine)


@pytest.fixture(autouse=True)
def database():
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


class MockFredAdapter:
    def series(self, series_id):
        return {
            "id": series_id, "title": "CPI", "frequency": "Monthly", "frequency_short": "M",
            "units": "Index", "units_short": "Index", "seasonal_adjustment": "Seasonally Adjusted",
            "seasonal_adjustment_short": "SA", "last_updated": "2025-03-12 07:45:00-05",
        }

    def observations(self, series_id, output_type, observation_start, chunk_vintages=False):
        if output_type == 4:
            return [{"date": "2025-01-01", "realtime_start": "2025-02-12", "value": "319.1"}]
        return [
            {"date": "2025-01-01", "realtime_start": "2025-02-12", "value": "319.1"},
            {"date": "2025-01-01", "realtime_start": "2025-03-12", "value": "319.2"},
        ]

    def current_observations(self, series_id, observation_start):
        return []


def test_vintages_and_no_lookahead():
    db = Session()
    service = FredCollectionService(MockFredAdapter())
    result = service.sync(db, ["CPIAUCSL"], date(2025, 1, 1))
    assert result["observations"] == 2
    assert db.query(MacroSeries).count() == 1
    rows = db.query(MacroObservation).order_by(MacroObservation.vintage_date).all()
    assert rows[0].is_initial_release is True
    assert rows[1].is_initial_release is False
    assert service.regime_dataset(db, date(2025, 2, 11)) == {}
    assert service.regime_dataset(db, date(2025, 2, 12))["CPIAUCSL"] == 319.1
    assert service.regime_dataset(db, date(2025, 3, 12))["CPIAUCSL"] == 319.2
    db.close()


def test_sync_is_idempotent():
    db = Session()
    service = FredCollectionService(MockFredAdapter())
    service.sync(db, ["CPIAUCSL"], date(2025, 1, 1))
    second = service.sync(db, ["CPIAUCSL"], date(2025, 1, 1))
    assert second["observations"] == 0
    assert db.query(MacroObservation).count() == 2
    db.close()


def test_wide_revision_rows_keep_only_value_changes():
    rows = FredAdapter._revision_rows("CPI", [{
        "date": "2025-01-01", "CPI_20250201": "100.0",
        "CPI_20250301": "100.0", "CPI_20250401": "100.2",
    }])
    assert rows == [
        {"date": "2025-01-01", "realtime_start": "2025-02-01", "value": "100.0"},
        {"date": "2025-01-01", "realtime_start": "2025-04-01", "value": "100.2"},
    ]


def test_macro_sync_reports_missing_api_key(monkeypatch):
    monkeypatch.setattr(macro_endpoint.settings, "FRED_API_KEY", "")

    with pytest.raises(HTTPException) as exc_info:
        macro_endpoint.sync_macro(db=None)

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "FRED_API_KEY is not configured"