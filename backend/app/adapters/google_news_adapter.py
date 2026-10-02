import hashlib
import json
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
from xml.etree import ElementTree

import httpx

from app.adapters.base import AdapterError
from app.adapters.gdelt_news_adapter import NewsArticleRecord


class GoogleNewsRssAdapter:
    base_url = "https://news.google.com/rss/search"
    allowed_sources = {
        "Reuters", "CNBC", "Bloomberg", "Axios",
        "The Associated Press", "AP News", "The Hill",
    }
    default_query = (
        '("Federal Reserve" OR "interest rates" OR inflation OR recession '
        'OR tariffs OR sanctions OR semiconductor OR "artificial intelligence" '
        'OR "crude oil" OR "corporate earnings") '
        '(stocks OR market OR economy OR trade OR technology) '
        '(source:Reuters OR source:CNBC OR source:Bloomberg OR source:Axios '
        'OR source:"Associated Press" OR source:"The Hill") when:1d'
    )

    def __init__(self, timeout: float = 30.0, now: datetime | None = None):
        self.timeout = timeout
        self.now = now or datetime.now(timezone.utc)

    @staticmethod
    def _timespan_delta(timespan: str) -> timedelta:
        units = {"min": "minutes", "h": "hours", "d": "days", "w": "weeks"}
        unit = next((item for item in units if timespan.endswith(item)), None)
        if unit is None:
            raise AdapterError(f"Unsupported news timespan: {timespan}")
        value = int(timespan[:-len(unit)])
        return timedelta(**{units[unit]: value})

    @staticmethod
    def _repair_text(value: str) -> str:
        if not any(marker in value for marker in ("â", "Ã", "Â")):
            return value
        try:
            return value.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return value

    def fetch(self, timespan: str = "24h", max_records: int = 100,
              query: str | None = None) -> list[NewsArticleRecord]:
        search_query = query or self.default_query
        if query is None and timespan.endswith("d"):
            search_query = search_query.replace("when:1d", f"when:{timespan}")
        try:
            response = httpx.get(
                self.base_url,
                params={"q": search_query, "hl": "en-US", "gl": "US", "ceid": "US:en"},
                headers={"User-Agent": "AlphaChain/1.0"}, timeout=self.timeout,
            )
            response.raise_for_status()
            root = ElementTree.fromstring(response.content)
        except (httpx.HTTPError, ElementTree.ParseError) as exc:
            raise AdapterError("Google News RSS request failed") from exc

        records = []
        cutoff = self.now - self._timespan_delta(timespan)
        for item in root.findall("./channel/item"):
            title = self._repair_text((item.findtext("title") or "").strip())
            url = (item.findtext("link") or "").strip()
            published = (item.findtext("pubDate") or "").strip()
            source_node = item.find("source")
            if not title or not url or not published:
                continue
            metadata = {
                "source_name": (source_node.text or "").strip() if source_node is not None else None,
                "source_url": source_node.get("url") if source_node is not None else None,
                "guid": item.findtext("guid"),
            }
            if metadata["source_name"] not in self.allowed_sources:
                continue
            published_at = parsedate_to_datetime(published).astimezone(timezone.utc)
            if published_at < cutoff or published_at > self.now + timedelta(minutes=5):
                continue
            raw_hash = hashlib.sha256(
                json.dumps(metadata | {"title": title, "url": url, "published": published},
                           ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest()
            source_url = metadata["source_url"] or url
            records.append(NewsArticleRecord(
                source="Google News RSS",
                external_id=hashlib.sha256(url.encode()).hexdigest(),
                title=title, url=url, domain=urlparse(source_url).netloc or None,
                language="English", source_country=None,
                published_at=published_at.replace(tzinfo=None),
                image_url=None, raw_hash=raw_hash, raw_metadata=metadata,
            ))
            if len(records) >= max_records:
                break
        return records


class FallbackNewsAdapter:
    def __init__(self, primary, fallback):
        self.primary = primary
        self.fallback = fallback

    def fetch(self, timespan: str = "24h", max_records: int = 100):
        try:
            return self.primary.fetch(timespan=timespan, max_records=max_records)
        except AdapterError:
            return self.fallback.fetch(timespan=timespan, max_records=max_records)