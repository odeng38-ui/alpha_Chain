import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from app.adapters.base import AdapterError


@dataclass(frozen=True)
class NewsArticleRecord:
    external_id: str
    title: str
    url: str
    domain: str | None
    language: str | None
    source_country: str | None
    published_at: datetime
    image_url: str | None
    raw_hash: str
    raw_metadata: dict


class GdeltNewsAdapter:
    base_url = "https://api.gdeltproject.org/api/v2/doc/doc"
    default_query = (
        '(("Federal Reserve" OR "interest rates" OR inflation OR recession '
        'OR tariffs OR sanctions OR semiconductor OR "artificial intelligence" '
        'OR "crude oil" OR "corporate earnings") AND '
        '(stocks OR market OR economy OR trade OR technology)) '
        'sourcecountry:US sourcelang:english'
    )

    def __init__(self, timeout: float = 30.0, max_attempts: int = 3):
        self.timeout = timeout
        self.max_attempts = max_attempts

    @staticmethod
    def _published(value: str) -> datetime:
        text = (value or "").strip()
        for pattern in ("%Y%m%dT%H%M%SZ", "%Y%m%d%H%M%S"):
            try:
                return datetime.strptime(text, pattern).replace(tzinfo=timezone.utc).replace(tzinfo=None)
            except ValueError:
                continue
        raise AdapterError(f"Unsupported GDELT publication date: {value}")

    def fetch(self, timespan: str = "24h", max_records: int = 100,
              query: str | None = None) -> list[NewsArticleRecord]:
        params = {"query": query or self.default_query, "mode": "artlist",
                  "format": "json", "sort": "datedesc",
                  "timespan": timespan, "maxrecords": max_records}
        response = None
        for attempt in range(self.max_attempts):
            response = httpx.get(
                self.base_url, params=params,
                headers={"User-Agent": "AlphaChain/1.0"}, timeout=self.timeout,
            )
            if response.status_code != 429 or attempt == self.max_attempts - 1:
                break
            retry_after = response.headers.get("Retry-After", "")
            delay = float(retry_after) if retry_after.isdigit() else 2 ** attempt
            time.sleep(min(delay, 10.0))
        assert response is not None
        try:
            response.raise_for_status()
            articles = response.json().get("articles") or []
        except (httpx.HTTPError, ValueError, AttributeError) as exc:
            raise AdapterError("GDELT news request failed") from exc
        records = []
        for article in articles:
            url = (article.get("url") or "").strip()
            title = (article.get("title") or "").strip()
            if not url or not title:
                continue
            canonical = url.split("#", 1)[0]
            raw_hash = hashlib.sha256(
                json.dumps(article, ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest()
            records.append(NewsArticleRecord(
                external_id=hashlib.sha256(canonical.encode()).hexdigest(),
                title=title, url=canonical, domain=article.get("domain"),
                language=article.get("language"),
                source_country=article.get("sourcecountry"),
                published_at=self._published(article.get("seendate") or ""),
                image_url=article.get("socialimage") or None,
                raw_hash=raw_hash, raw_metadata=article,
            ))
        return records