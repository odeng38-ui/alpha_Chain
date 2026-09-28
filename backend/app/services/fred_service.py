import hashlib
import json
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Dict, Iterable

from sqlalchemy.orm import Session

from app.adapters.fred_adapter import FredAdapter, FredApiError
from app.models.schema import MacroObservation, MacroSeries

DEFAULT_SERIES = (
    "FEDFUNDS", "CPIAUCSL", "PCEPILFE", "UNRATE", "DGS2",
    "DGS10", "T10Y2Y", "INDPRO", "RSAFS", "M2SL",
)


def _datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


class FredCollectionService:
    def __init__(self, adapter: FredAdapter):
        self.adapter = adapter

    def sync(self, db: Session, series_ids: Iterable[str] = DEFAULT_SERIES,
             observation_start: date = date(2000, 1, 1)) -> Dict[str, int]:
        result = {"series": 0, "observations": 0, "revisions": 0, "metadata_changes": 0}
        for series_id in series_ids:
            meta = self.adapter.series(series_id)
            identity = {key: meta.get(key, "") for key in (
                "title", "frequency", "frequency_short", "units", "units_short",
                "seasonal_adjustment", "seasonal_adjustment_short",
            )}
            metadata_hash = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
            series = db.get(MacroSeries, series_id)
            if series and series.metadata_hash != metadata_hash:
                result["metadata_changes"] += 1
            if series is None:
                series = MacroSeries(series_id=series_id)
                db.add(series)
            for key, value in identity.items():
                setattr(series, key, value)
            series.metadata_hash = metadata_hash
            series.last_updated = _datetime(meta["last_updated"]) if meta.get("last_updated") else None
            result["series"] += 1

            chunk_vintages = meta.get("frequency_short") == "D"
            try:
                rows = self.adapter.observations(series_id, 3, observation_start, chunk_vintages)
                initial_keys = {
                    (row["date"], row["realtime_start"])
                    for row in self.adapter.observations(series_id, 4, observation_start, chunk_vintages)
                }
            except FredApiError:
                if not chunk_vintages:
                    raise
                rows = self.adapter.current_observations(series_id, observation_start)
                initial_keys = {(row["date"], row["realtime_start"]) for row in rows}
            for row in rows:
                if row.get("value") in (None, "."):
                    continue
                observation_date = date.fromisoformat(row["date"])
                vintage_date = date.fromisoformat(row["realtime_start"])
                existing = db.get(MacroObservation, (series_id, observation_date, vintage_date))
                try:
                    value = Decimal(row["value"])
                except InvalidOperation:
                    continue
                if existing is None:
                    existing = MacroObservation(
                        series_id=series_id, observation_date=observation_date,
                        vintage_date=vintage_date,
                    )
                    db.add(existing)
                    result["observations"] += 1
                    if vintage_date > observation_date:
                        result["revisions"] += 1
                existing.value = value
                existing.available_at = datetime.combine(vintage_date, time(23, 59, 59))
                existing.is_initial_release = (row["date"], row["realtime_start"]) in initial_keys
            db.commit()
        return result

    @staticmethod
    def regime_dataset(db: Session, as_of: date) -> Dict[str, float]:
        cutoff = datetime.combine(as_of, time.max)
        result = {}
        for series_id in DEFAULT_SERIES:
            row = db.query(MacroObservation).filter(
                MacroObservation.series_id == series_id,
                MacroObservation.available_at <= cutoff,
            ).order_by(
                MacroObservation.observation_date.desc(),
                MacroObservation.vintage_date.desc(),
            ).first()
            if row and row.value is not None:
                result[series_id] = float(row.value)
        return result
