import hashlib
import re
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, Iterable, Optional

from sqlalchemy.orm import Session

from app.adapters.dart_adapter import DartAdapter
from app.models.schema import (
    Company,
    DartSyncState,
    DisclosureEvent,
    Filing,
    FinancialFact,
)

PARSER_VERSION = "dart_v1"
REPORT_CODES = {"11013": "Q1", "11012": "H1", "11014": "Q3", "11011": "FY"}
EVENT_RULES = (
    ("SUPPLY_CONTRACT", re.compile(r"공급계약|판매계약")),
    ("FACILITY_INVESTMENT", re.compile(r"신규시설투자|시설투자")),
    ("CAPITAL_INCREASE", re.compile(r"유상증자")),
    ("CONVERTIBLE_BOND", re.compile(r"전환사채")),
    ("BOND_WITH_WARRANT", re.compile(r"신주인수권부사채")),
    ("TREASURY_STOCK", re.compile(r"자기주식|자사주")),
)


def classify_event(report_name: str) -> Optional[str]:
    for event_type, pattern in EVENT_RULES:
        if pattern.search(report_name):
            return event_type
    return None


def _base_report_name(name: str) -> str:
    return re.sub(r"^\s*\[(기재)?정정\]\s*|^\s*정정신고\s*", "", name).strip()


def _number(value: Optional[str]) -> Optional[Decimal]:
    if not value or value.strip() in {"-", ""}:
        return None
    text = value.replace(",", "").strip()
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    try:
        result = Decimal(text)
        return -result if negative else result
    except InvalidOperation:
        return None


class DartCollectionService:
    def __init__(self, adapter: DartAdapter, raw_dir: str = "./data/dart"):
        self.adapter = adapter
        self.raw_dir = Path(raw_dir)

    def sync_company(self, db: Session, company: Company, start: date, end: date,
                     download_documents: bool = False) -> Dict[str, int]:
        if not company.corp_code:
            raise ValueError("company has no DART corp_code")
        rows = self.adapter.list_filings(company.corp_code, start, end)
        inserted = corrected = events = 0
        for row in rows:
            receipt = row["rcept_no"]
            filing = db.get(Filing, receipt)
            if filing is None:
                filed_date = datetime.strptime(row["rcept_dt"], "%Y%m%d").date()
                available_at = datetime.combine(filed_date, time(23, 59, 59))
                filing = Filing(
                    rcept_no=receipt, company_id=company.id,
                    title=row["report_nm"], report_name=row["report_nm"],
                    filed_at=available_at, available_at=available_at,
                    report_code=row.get("pblntf_detail_ty"),
                    raw_ref=f"https://opendart.fss.or.kr/api/document.xml?rcept_no={receipt}",
                    parser_version=PARSER_VERSION,
                )
                base_name = _base_report_name(row["report_nm"])
                if base_name != row["report_nm"].strip():
                    original = db.query(Filing).filter(
                        Filing.company_id == company.id,
                        Filing.rcept_no != receipt,
                        Filing.report_name.contains(base_name),
                    ).order_by(Filing.filed_at.desc()).first()
                    if original:
                        filing.correction_of = original.rcept_no
                        corrected += 1
                db.add(filing)
                db.flush()
                inserted += 1
                event_type = classify_event(row["report_nm"])
                if event_type:
                    db.add(DisclosureEvent(
                        filing_id=receipt, company_id=company.id,
                        event_type=event_type, event_date=available_at,
                        rule_version="dart_title_v1", raw_data={"report_name": row["report_nm"]},
                    ))
                    events += 1
            if download_documents and not filing.raw_hash:
                content = self.adapter.download_document(receipt)
                target = self.raw_dir / company.corp_code / f"{receipt}.zip"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                filing.raw_ref = str(target)
                filing.raw_hash = hashlib.sha256(content).hexdigest()

        state = db.get(DartSyncState, company.id) or DartSyncState(company_id=company.id)
        db.add(state)
        state.last_filing_date = end
        state.status = "SUCCESS"
        state.last_error = None
        db.commit()
        return {"filings": inserted, "corrections": corrected, "events": events}

    def sync_financials(self, db: Session, company: Company, year: str,
                        report_code: str, fs_divs: Iterable[str] = ("CFS", "OFS")) -> int:
        filing = db.query(Filing).filter(
            Filing.company_id == company.id,
            Filing.title.contains(year),
        ).order_by(Filing.available_at.desc()).first()
        count = 0
        for fs_div in fs_divs:
            for row in self.adapter.financial_statements(company.corp_code, year, report_code, fs_div):
                period = f"{year}:{report_code}:{row.get('thstrm_nm', '')}"
                row_identity = "|".join(str(row.get(key) or "") for key in (
                    "sj_div", "account_id", "account_nm", "account_detail", "ord"
                ))
                row_key = hashlib.sha256(row_identity.encode("utf-8")).hexdigest()
                exists = db.query(FinancialFact).filter_by(
                    company_id=company.id,
                    filing_id=filing.rcept_no if filing else None,
                    fs_div=fs_div,
                    row_key=row_key,
                ).first()
                if exists:
                    continue
                db.add(FinancialFact(
                    company_id=company.id, filing_id=filing.rcept_no if filing else None,
                    business_year=year, report_code=report_code, fs_div=fs_div,
                    statement_div=row.get("sj_div"), account_detail=row.get("account_detail"),
                    row_key=row_key, period=period,
                    account_id=row.get("account_id") or row.get("account_nm"),
                    account_name=row.get("account_nm") or "UNKNOWN",
                    value=_number(row.get("thstrm_amount")), unit=row.get("currency") or "KRW",
                    consolidated=fs_div == "CFS",
                    filed_at=filing.available_at if filing else datetime.combine(date(int(year), 12, 31), time.max),
                ))
                count += 1
        db.commit()
        return count
