from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base, get_db
from app.main import app
from app.models.schema import Company, IdentifierMap, Security
from app.services.master_service import CompanySecurityMasterService
from app.services.report_service import MappingReportService

SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)



@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


def override_get_db():
    try:
        db = TestingSessionLocal()
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)


def test_security_type_classification():
    assert CompanySecurityMasterService.classify_security_type("삼성전자", "005930") == "COMMON"
    assert CompanySecurityMasterService.classify_security_type("SK하이닉스", "000660") == "COMMON"
    assert CompanySecurityMasterService.classify_security_type("삼성전자우", "005935") == "PREFERRED"
    assert CompanySecurityMasterService.classify_security_type("현대차2우B", "005387") == "PREFERRED"
    assert CompanySecurityMasterService.classify_security_type("KODEX 200", "069500") == "ETF"
    assert CompanySecurityMasterService.classify_security_type("TIGER 미국S&P500", "360750") == "ETF"
    assert CompanySecurityMasterService.classify_security_type("메리츠 KOSPI200 ETN", "580001") == "ETN"
    assert CompanySecurityMasterService.classify_security_type("하나금융30호스팩", "484340") == "SPAC"


def test_upsert_master_record_and_identifier_map():
    db = TestingSessionLocal()

    rec = {
        "corp_code": "00126380",
        "name": "삼성전자",
        "ticker": "005930",
        "market": "KOSPI",
        "isin": "KR7005930003",
        "listed_at": date(1975, 6, 11)
    }

    company, security, is_new = CompanySecurityMasterService.upsert_master_record(db, rec)
    db.commit()

    assert is_new is True
    assert company.name == "삼성전자"
    assert security.ticker == "005930"
    assert security.security_type == "COMMON"

    # 식별자 매핑 확인
    id_maps = db.query(IdentifierMap).filter(IdentifierMap.company_id == company.id).all()
    sources = {im.source for im in id_maps}
    assert "DART_CORP_CODE" in sources
    assert "KRX_TICKER" in sources
    assert "ISIN" in sources

    # 멱등성 검증 (동일 데이터 재입력)
    _, _, is_new_2 = CompanySecurityMasterService.upsert_master_record(db, rec)
    db.commit()
    assert is_new_2 is False

    db.close()


def test_delisting_and_effective_dates():
    db = TestingSessionLocal()

    rec = {
        "corp_code": "00999999",
        "name": "폐업기업",
        "ticker": "999999",
        "market": "KOSDAQ",
        "status": "DELISTED",
        "delisted_at": date(2024, 1, 15)
    }

    company, security, _ = CompanySecurityMasterService.upsert_master_record(db, rec)
    db.commit()

    assert company.status == "DELISTED"
    assert security.delisted_at == date(2024, 1, 15)
    assert security.effective_to == date(2024, 1, 15)

    db.close()


def test_mapping_report_service_99pct_threshold():
    db = TestingSessionLocal()

    records = []
    # 99개 정상 보통주 매핑 데이터
    for i in range(1, 100):
        records.append({
            "corp_code": f"{i:08d}",
            "name": f"기업_{i}",
            "ticker": f"{i:06d}",
            "market": "KOSPI",
            "isin": f"KR7{i:09d}"
        })

    CompanySecurityMasterService.sync_master_records(db, records)

    # 1개 미매핑 보통주 추가 (DART corp_code가 없는 기업)
    c_unmapped = Company(corp_code=None, name="미매핑기업")
    db.add(c_unmapped)
    db.flush()

    unmapped_sec = Security(
        company_id=c_unmapped.id,
        market="KOSPI",
        ticker="998877",
        security_type="COMMON"
    )
    db.add(unmapped_sec)
    db.commit()


    report = MappingReportService.generate_mapping_report(db)
    summary = report["summary"]

    assert summary["total_common_securities"] == 100
    assert summary["mapped_common_securities"] == 99
    assert summary["mapping_rate_percent"] == 99.0
    assert summary["target_99pct_met"] is True
    assert len(report["unmapped_list"]) == 1

    db.close()


def test_master_and_admin_api_endpoints():
    # 1. Master sync API
    payload = [
        {
            "corp_code": "00126380",
            "name": "삼성전자",
            "ticker": "005930",
            "market": "KOSPI",
            "isin": "KR7005930003"
        }
    ]
    resp = client.post("/api/v1/master/sync", json=payload)
    assert resp.status_code == 200
    assert resp.json()["inserted"] == 1

    # 2. Company search API
    resp_search = client.get("/api/v1/master/companies?query=삼성전자")
    assert resp_search.status_code == 200
    data = resp_search.json()["data"]
    assert len(data) == 1
    assert data[0]["corp_code"] == "00126380"
    company_id = data[0]["company_id"]
    security_id = data[0]["securities"][0]["security_id"]

    # 3. Report API
    resp_report = client.get("/api/v1/master/report")
    assert resp_report.status_code == 200
    assert resp_report.json()["summary"]["mapping_rate_percent"] == 100.0

    # 4. Admin Manual Identifier Creation API
    resp_admin = client.post("/api/v1/admin/identifiers", json={
        "company_id": company_id,
        "security_id": security_id,
        "source": "NAVER_CODE",
        "source_id_value": "005930",
        "is_primary": True
    })
    assert resp_admin.status_code == 200
    assert resp_admin.json()["identifier_map"]["source"] == "NAVER_CODE"

    # 5. Admin Security Update API
    resp_update = client.put(f"/api/v1/admin/securities/{security_id}", json={
        "market": "KOSPI",
        "security_type": "COMMON"
    })
    assert resp_update.status_code == 200
    assert resp_update.json()["security"]["security_type"] == "COMMON"


def test_ticker_reuse_preserves_security_history():
    db = TestingSessionLocal()
    CompanySecurityMasterService.sync_master_records(db, [{
        "corp_code": "00000001", "name": "과거회사", "ticker": "123456",
        "market": "KOSPI", "listed_at": date(2000, 1, 1),
    }])
    CompanySecurityMasterService.sync_master_records(db, [{
        "corp_code": "00000002", "name": "신규회사", "ticker": "123456",
        "market": "KOSDAQ", "listed_at": date(2025, 1, 1),
    }])
    rows = db.query(Security).filter(Security.ticker == "123456").order_by(Security.id).all()
    assert len(rows) == 2
    assert rows[0].effective_to == date(2025, 1, 1)
    assert rows[1].effective_to is None
    assert rows[0].company_id != rows[1].company_id
    db.close()


def test_empty_mapping_report_does_not_pass():
    db = TestingSessionLocal()
    report = MappingReportService.generate_mapping_report(db)
    assert report["summary"]["mapping_rate_percent"] == 0.0
    assert report["summary"]["target_99pct_met"] is False
    db.close()
