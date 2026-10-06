from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1.endpoints import dart as dart_endpoint
from app.db.session import Base
from app.models.schema import Company, DisclosureEvent, Filing, FinancialFact, Security
from app.services.dart_service import DartCollectionService, classify_event

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Session = sessionmaker(bind=engine)


@pytest.fixture(autouse=True)
def database():
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


class MockDartAdapter:
    def list_filings(self, corp_code, start, end):
        return [
            {"rcept_no": "20250101000001", "rcept_dt": "20250101", "report_nm": "단일판매ㆍ공급계약체결", "pblntf_detail_ty": "B001"},
            {"rcept_no": "20250102000002", "rcept_dt": "20250102", "report_nm": "[기재정정] 단일판매ㆍ공급계약체결", "pblntf_detail_ty": "B001"},
        ]

    def financial_statements(self, corp_code, year, report_code, fs_div):
        return [{
            "account_id": "ifrs-full_Revenue", "account_nm": "매출액",
            "sj_div": "IS", "thstrm_nm": "제 10 기", "thstrm_amount": "1,234,567",
            "currency": "KRW", "ord": "1", "account_detail": "-",
        }]

    def download_document(self, receipt_no):
        return b"zip-content"


def test_event_classifier_dataset():
    cases = {
        "단일판매ㆍ공급계약체결": "SUPPLY_CONTRACT",
        "신규시설투자등": "FACILITY_INVESTMENT",
        "유상증자결정": "CAPITAL_INCREASE",
        "전환사채권발행결정": "CONVERTIBLE_BOND",
        "신주인수권부사채권발행결정": "BOND_WITH_WARRANT",
        "자기주식취득결정": "TREASURY_STOCK",
        "임원ㆍ주요주주특정증권등소유상황보고서": None,
    }
    assert {name: classify_event(name) for name in cases} == cases


def test_filing_correction_lineage_and_idempotency():
    db = Session()
    company = Company(corp_code="00126380", name="삼성전자")
    db.add(company)
    db.commit()
    service = DartCollectionService(MockDartAdapter(), "./data/test_dart")
    first = service.sync_company(db, company, date(2025, 1, 1), date(2025, 1, 2), True)
    second = service.sync_company(db, company, date(2025, 1, 1), date(2025, 1, 2), True)
    correction = db.get(Filing, "20250102000002")
    assert first == {"filings": 2, "corrections": 1, "events": 2}
    assert second["filings"] == 0
    assert correction.correction_of == "20250101000001"
    assert correction.raw_hash is not None
    assert db.query(Filing).count() == 2
    assert db.query(DisclosureEvent).count() == 2
    db.close()


def test_financial_cfs_ofs_are_preserved():
    db = Session()
    company = Company(corp_code="00126380", name="삼성전자")
    db.add(company)
    db.commit()
    service = DartCollectionService(MockDartAdapter())
    service.sync_company(db, company, date(2025, 1, 1), date(2025, 1, 2))
    assert service.sync_financials(db, company, "2025", "11011") == 2
    rows = db.query(FinancialFact).all()
    assert {row.fs_div for row in rows} == {"CFS", "OFS"}
    assert all(row.value == 1234567 for row in rows)
    assert service.sync_financials(db, company, "2025", "11011") == 0
    db.close()


def test_sync_dart_uses_active_company_cursor(monkeypatch):
    db = Session()
    companies = [
        Company(corp_code=f"0000000{index}", name=f"Company {index}")
        for index in range(1, 4)
    ]
    db.add_all(companies)
    db.flush()
    db.add_all([
        Security(company_id=companies[0].id, ticker="000001", market="KOSPI"),
        Security(
            company_id=companies[1].id,
            ticker="000002",
            market="KOSPI",
            effective_to=date(2025, 1, 1),
        ),
        Security(company_id=companies[2].id, ticker="000003", market="KOSPI"),
    ])
    db.commit()

    class FakeService:
        def __init__(self, adapter, raw_dir):
            pass

        def sync_company(self, db, company, start, end, download_documents):
            return {"filings": company.id, "corrections": 0, "events": 0}

    monkeypatch.setattr(dart_endpoint, "DartAdapter", lambda key: object())
    monkeypatch.setattr(dart_endpoint, "DartCollectionService", FakeService)
    result = dart_endpoint.sync_dart(
        dart_endpoint.DartSyncRequest(
            after_id=companies[0].id,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 2),
            limit=1,
        ),
        db,
    )

    assert result["companies"] == 1
    assert result["results"][0]["company_id"] == companies[2].id
    assert result["next_after_id"] == companies[2].id
    assert result["has_more"] is False
    db.close()