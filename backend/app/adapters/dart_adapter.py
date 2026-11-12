import time
from datetime import date
from typing import Any, Dict, List, Optional

import httpx


class DartApiError(RuntimeError):
    def __init__(self, message: str, status_code: Optional[str] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class DartAuthError(DartApiError):
    """Non-retryable authentication failure (status 010, 011, 012)."""
    pass


class DartRateLimitError(DartApiError):
    """Daily request limit reached (status 020)."""
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

                # Fast check for non-retryable OpenDART status
                content_type = response.headers.get("content-type", "")
                if "json" in content_type:
                    try:
                        data = response.json()
                        st = data.get("status")
                        if st in ("010", "011", "012"):
                            raise DartAuthError(f"DART Auth Error ({st}): {data.get('message')}", status_code=st)
                        elif st == "020":
                            raise DartRateLimitError(f"DART Rate Limit Exceeded (020): {data.get('message')}", status_code=st)
                    except (ValueError, KeyError):
                        pass
                return response
            except (DartAuthError, DartRateLimitError):
                raise
            except httpx.HTTPError as exc:
                last_error = exc
                time.sleep(2 ** attempt)
        raise DartApiError(str(last_error)) from last_error

    def _json(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        payload = self._request(path, params).json()
        status = payload.get("status")
        if status in ("010", "011", "012"):
            raise DartAuthError(f"DART Auth Error ({status}): {payload.get('message')}", status_code=status)
        if status == "020":
            raise DartRateLimitError(f"DART Rate Limit Exceeded (020): {payload.get('message')}", status_code=status)
        if status == "013":
            return {"status": "013", "message": payload.get("message", "조회된 데이터가 없습니다"), "list": [], "total_count": 0, "total_page": 0}
        if status != "000":
            raise DartApiError(f"DART {status}: {payload.get('message')}", status_code=status)
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

    def list_recent_filings(
        self,
        start: date,
        end: date,
        *,
        corp_code: Optional[str] = None,
        page_count: int = 100,
        max_pages: int = 10,
    ) -> List[Dict[str, Any]]:
        """Fetch market-wide or company-specific disclosures with pagination and no omissions."""
        page = 1
        rows: List[Dict[str, Any]] = []
        params: Dict[str, Any] = {
            "bgn_de": start.strftime("%Y%m%d"),
            "end_de": end.strftime("%Y%m%d"),
            "last_reprt_at": "N",
            "page_count": min(max(page_count, 10), 100),
            "sort": "date",
            "sort_mth": "asc",
        }
        if corp_code:
            params["corp_code"] = corp_code

        while page <= max_pages:
            params["page_no"] = page
            payload = self._json("list.json", params)
            if payload.get("status") == "013":
                break

            items = payload.get("list") or []
            rows.extend(items)

            total_pages = int(payload.get("total_page") or 1)
            if page >= total_pages or not items:
                break
            page += 1

        return rows

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
            st = payload.get("status")
            if st in ("010", "011", "012"):
                raise DartAuthError(f"DART Auth Error ({st}): {payload.get('message')}", status_code=st)
            if st == "020":
                raise DartRateLimitError(f"DART Rate Limit Exceeded (020): {payload.get('message')}", status_code=st)
            raise DartApiError(f"DART {st}: {payload.get('message')}", status_code=st)
        return response.content
