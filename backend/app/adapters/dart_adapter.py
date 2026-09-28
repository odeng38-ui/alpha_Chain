import time
from datetime import date
from typing import Any, Dict, List

import httpx


class DartApiError(RuntimeError):
    pass


class DartAdapter:
    base_url = "https://opendart.fss.or.kr/api"

    def __init__(self, api_key: str, timeout: float = 30, retries: int = 3):
        if not api_key:
            raise ValueError("DART_API_KEY is required")
        self.api_key = api_key
        self.timeout = timeout
        self.retries = retries
        self.client = httpx.Client(timeout=timeout)

    def _request(self, path: str, params: Dict[str, Any]) -> httpx.Response:
        params = {"crtfc_key": self.api_key, **params}
        last_error = None
        for attempt in range(self.retries):
            try:
                response = self.client.get(f"{self.base_url}/{path}", params=params)
                response.raise_for_status()
                return response
            except httpx.HTTPError as exc:
                last_error = exc
                time.sleep(2 ** attempt)
        raise DartApiError(str(last_error)) from last_error

    def _json(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        payload = self._request(path, params).json()
        status = payload.get("status")
        if status not in ("000", "013"):
            raise DartApiError(f"DART {status}: {payload.get('message')}")
        return payload

    def company_profile(self, corp_code: str) -> Dict[str, Any]:
        return self._json("company.json", {"corp_code": corp_code})
    def list_filings(self, corp_code: str, start: date, end: date) -> List[Dict[str, Any]]:
        page = 1
        rows: List[Dict[str, Any]] = []
        while True:
            payload = self._json("list.json", {
                "corp_code": corp_code,
                "bgn_de": start.strftime("%Y%m%d"),
                "end_de": end.strftime("%Y%m%d"),
                "last_reprt_at": "N",
                "page_no": page,
                "page_count": 100,
                "sort": "date",
                "sort_mth": "asc",
            })
            rows.extend(payload.get("list") or [])
            if page >= int(payload.get("total_page") or 1):
                return rows
            page += 1

    def financial_statements(self, corp_code: str, year: str,
                             report_code: str, fs_div: str) -> List[Dict[str, Any]]:
        payload = self._json("fnlttSinglAcntAll.json", {
            "corp_code": corp_code,
            "bsns_year": year,
            "reprt_code": report_code,
            "fs_div": fs_div,
        })
        return payload.get("list") or []

    def download_document(self, receipt_no: str) -> bytes:
        response = self._request("document.xml", {"rcept_no": receipt_no})
        content_type = response.headers.get("content-type", "")
        if "json" in content_type:
            payload = response.json()
            raise DartApiError(f"DART {payload.get('status')}: {payload.get('message')}")
        return response.content
