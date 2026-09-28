from datetime import date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
from app.models.schema import (
    Company,
    Filing,
    Relationship,
    RelationshipEvidence,
    ScoreSnapshot,
    Security,
)

SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)

def test_company_and_security_creation():
    db = SessionLocal()
    company = Company(corp_code="00126380", name="삼성전자", status="ACTIVE", industry_id="반도체")
    db.add(company)
    db.commit()
    db.refresh(company)

    assert company.id is not None
    assert company.corp_code == "00126380"

    security = Security(
        company_id=company.id,
        market="KOSPI",
        ticker="005930",
        isin="KR7005930003",
        security_type="COMMON",
        listed_at=date(1975, 6, 11)
    )
    db.add(security)
    db.commit()
    db.refresh(security)

    assert security.id is not None
    assert security.company.name == "삼성전자"
    db.close()

def test_relationship_and_evidence():
    db = SessionLocal()
    c1 = Company(corp_code="00126380", name="삼성전자")
    c2 = Company(corp_code="00164779", name="ASML Korea")
    db.add_all([c1, c2])
    db.commit()

    rel = Relationship(
        source_id=c2.id,
        target_id=c1.id,
        type="SUPPLIES_TO",
        status="verified",
        confidence=0.95
    )
    db.add(rel)
    db.commit()

    filing = Filing(
        rcept_no="20230315000001",
        company_id=c1.id,
        title="사업보고서 (2022.12)",
        filed_at=datetime(2023, 3, 15, 16, 0)
    )
    db.add(filing)
    db.commit()

    evidence = RelationshipEvidence(
        relationship_id=rel.id,
        filing_id=filing.rcept_no,
        excerpt="ASML로부터 반도체 노광장비(EUV)를 지속 공급받고 있음",
        published_at=datetime(2023, 3, 15),
        source_document="사업보고서",
        confidence=0.95,
        evidence_hash="a" * 64,
    )
    db.add(evidence)
    db.commit()

    assert rel.id is not None
    assert len(rel.evidences) == 1
    assert rel.evidences[0].excerpt.startswith("ASML")
    db.close()

def test_score_snapshot():
    db = SessionLocal()
    c = Company(corp_code="00126380", name="삼성전자")
    db.add(c)
    db.commit()

    s = Security(company_id=c.id, market="KOSPI", ticker="005930")
    db.add(s)
    db.commit()

    score = ScoreSnapshot(
        security_id=s.id,
        as_of_date=date(2026, 9, 18),
        horizon="20d",
        component={"macro": 75.0, "industry": 80.0, "fundamental": 85.0, "momentum": 70.0, "disclosure": 90.0, "chain": 82.0},
        total=80.3,
        confidence=0.92,
        version="v1.0"
    )
    db.add(score)
    db.commit()

    assert score.id is not None
    assert score.total == 80.3
    assert score.component["macro"] == 75.0
    db.close()
