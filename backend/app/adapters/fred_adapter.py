import time
from datetime import date, timedelta
from typing import Any, Dict, List

import httpx


class FredApiError(RuntimeError):
    pass


class FredAdapter:
    base_url = "https://api.stlouisfed.org/fred"

    def __init__(self, api_key: str, retries: int = 3):
        if not api_key:
            raise ValueError("FRED_API_KEY is required")
        self.api_key = api_key
        self.retries = retries
        self.client = httpx.Client(timeout=60)

    def _get(self, path: str, **params) -> Dict[str, Any]:
        last_error = None
        for attempt in range(self.retries):
            try:
                response = self.client.get(
                    f"{self.base_url}/{path}",
                    params={"api_key": self.api_key, "file_type": "json", **params},
                )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as exc:
                try:
                    message = exc.response.json().get("error_message", "FRED request failed")
                except ValueError:
                    message = f"FRED HTTP {exc.response.status_code}"
                last_error = FredApiError(message)
                time.sleep(2 ** attempt)
            except (httpx.HTTPError, ValueError) as exc:
                last_error = FredApiError(type(exc).__name__)
                time.sleep(2 ** attempt)
        raise last_error

    def series(self, series_id: str) -> Dict[str, Any]:
        rows = self._get("series", series_id=series_id).get("seriess") or []
        if not rows:
            raise FredApiError(f"series not found: {series_id}")
        return rows[0]

    def observations(self, series_id: str, output_type: int,
                     observation_start: date, chunk_vintages: bool = False) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        realtime_start = observation_start
        today = date.today() - timedelta(days=1)
        while realtime_start <= today:
            realtime_end = min(
                realtime_start + timedelta(days=1500), today
            ) if chunk_vintages else today
            payload = self._get(
                "series/observations", series_id=series_id,
                observation_start=observation_start.isoformat(),
                realtime_start=realtime_start.isoformat(),
                realtime_end=realtime_end.isoformat(),
                output_type=output_type, limit=100000,
            )
            observations = payload.get("observations") or []
            if output_type == 3:
                observations = self._revision_rows(series_id, observations)
            rows.extend(observations)
            if not chunk_vintages:
                break
            realtime_start = realtime_end + timedelta(days=1)
        return rows

    @staticmethod
    def _revision_rows(series_id: str, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized = []
        prefix = f"{series_id}_"
        for row in rows:
            previous = None
            for key in sorted(key for key in row if key.startswith(prefix)):
                value = row[key]
                if value in (None, ".") or value == previous:
                    continue
                vintage_date = date.fromisoformat(key.removeprefix(prefix)).isoformat()
                normalized.append({
                    "date": row["date"],
                    "realtime_start": vintage_date,
                    "value": value,
                })
                previous = value
        return normalized

    def current_observations(self, series_id: str,
                             observation_start: date) -> List[Dict[str, Any]]:
        payload = self._get(
            "series/observations", series_id=series_id,
            observation_start=observation_start.isoformat(),
            output_type=1, limit=100000,
        )
        rows = payload.get("observations") or []
        for row in rows:
            row["realtime_start"] = row["date"]
        return rows
