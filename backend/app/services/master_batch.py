import csv
import io
import os
import zipfile
from datetime import date
from typing import Any, Dict, Iterable, Optional
from xml.etree import ElementTree

import httpx
from sqlalchemy.orm import Session

from app.models.schema import Security
from app.services.master_service import CompanySecurityMasterService

DART_CORP_CODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml"


def fetch_dart_corporations(api_key: str) -> Dict[str, Dict[str, str]]:
    if not api_key:
        raise ValueError("DART_API_KEY is required")
    response = httpx.get(DART_CORP_CODE_URL, params={"crtfc_key": api_key}, timeout=60)
    response.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        xml_bytes = archive.read(archive.namelist()[0])
    root = ElementTree.fromstring(xml_bytes)
    result = {}
    for item in root.findall("list"):
        ticker = (item.findtext("stock_code") or "").strip()
        if ticker:
            result[ticker.zfill(6)] = {
                "corp_code": (item.findtext("corp_code") or "").strip(),
                "corp_name": (item.findtext("corp_name") or "").strip(),
            }
    return result


def fetch_krx_listings(as_of: date, krx_id: str = "", krx_pw: str = "") -> Iterable[Dict[str, str]]:
    if krx_id:
        os.environ["KRX_ID"] = krx_id
    if krx_pw:
        os.environ["KRX_PW"] = krx_pw
    from pykrx import stock

    date_text = as_of.strftime("%Y%m%d")
    for market in ("KOSPI", "KOSDAQ"):
        for ticker in stock.get_market_ticker_list(date_text, market=market):
            yield {
                "ticker": str(ticker).zfill(6),
                "name": stock.get_market_ticker_name(ticker),
                "market": market,
            }


def load_identifier_supplement(path: Optional[str]) -> Dict[str, Dict[str, str]]:
    if not path:
        return {}
    with open(path, newline="", encoding="utf-8-sig") as stream:
        rows = csv.DictReader(stream)
        return {
            str(row["ticker"]).zfill(6): row
            for row in rows
            if row.get("ticker")
        }


def sync_provider_master(db: Session, dart_api_key: str,
                         as_of: Optional[date] = None,
                         supplement_csv: Optional[str] = None,
                         krx_id: str = "", krx_pw: str = "",
                         offset: int = 0,
                         limit: Optional[int] = None) -> Dict[str, Any]:
    as_of = as_of or date.today()
    dart = fetch_dart_corporations(dart_api_key)
    supplement = load_identifier_supplement(supplement_csv)
    records = []
    unmapped = []
    seen_tickers = set()

    listings = [] if os.getenv("VERCEL") else list(fetch_krx_listings(as_of, krx_id, krx_pw))
    source = "KRX_DART"
    if not listings:
        source = "DART_FALLBACK"
        listings = [
            {"ticker": ticker, "name": corp["corp_name"], "market": "UNKNOWN"}
            for ticker, corp in sorted(dart.items())
        ]

    total_candidates = len(listings)
    selected_listings = listings[offset:offset + limit] if limit is not None else listings[offset:]

    for listing in selected_listings:
        ticker = listing["ticker"]
        seen_tickers.add(ticker)
        corp = dart.get(ticker)
        if not corp:
            unmapped.append({**listing, "reason": "MISSING_DART_CORP_CODE"})
            continue
        extra = supplement.get(ticker, {})
        records.append({
            **listing,
            "corp_code": corp["corp_code"],
            "name": corp["corp_name"] or listing["name"],
            "isin": extra.get("isin") or None,
            "eng_ticker": extra.get("eng_ticker") or None,
            "industry_id": extra.get("industry_id") or None,
            "listed_at": date.fromisoformat(extra["listed_at"]) if extra.get("listed_at") else None,
        })

    if source == "DART_FALLBACK":
        result = CompanySecurityMasterService.sync_master_records_bulk(db, records)
    else:
        result = CompanySecurityMasterService.sync_master_records(db, records)
    closed = 0
    is_full_sync = offset == 0 and limit is None
    if is_full_sync:
        active = db.query(Security).filter(Security.effective_to.is_(None)).all()
        for security in active:
            if security.ticker not in seen_tickers:
                security.effective_to = as_of
                security.delisted_at = security.delisted_at or as_of
                if security.company:
                    security.company.status = "DELISTED"
                for identifier in security.identifier_maps:
                    if identifier.effective_to is None:
                        identifier.effective_to = as_of
                closed += 1
    db.commit()
    next_offset = offset + len(selected_listings)
    return {
        **result,
        "source": source,
        "total_candidates": total_candidates,
        "next_offset": next_offset if next_offset < total_candidates else None,
        "has_more": next_offset < total_candidates,
        "closed": closed,
        "unmapped_count": len(unmapped),
        "unmapped": unmapped,
    }

