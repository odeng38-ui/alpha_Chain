from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.main import app
from app.models.schema import Company, Relationship, RelationshipEvidence
from app.services.relationship_service import (
    RelationshipCandidate,
    RelationshipExtractionService,
    normalize_company_name,
)

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Session = sessionmaker(bind=engine)


@pytest.fixture(autouse=True)
def database():
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


def test_normalize_company_name():
    assert normalize_company_name("(주) 삼성전자") == normalize_company_name("삼성전자 주식회사")


def test_rule_extraction_requires_evidence_and_accumulates_sources():
    db = Session()
    source = Company(name="알파부품")
    target = Company(name="베타전자")
    db.add_all([source, target])
    db.commit()
    service = RelationshipExtractionService()

    first = service.extract_and_persist(
        db, source.id, "알파부품은 베타전자에 반도체 부품을 공급합니다.",
        source_document="2025 사업보고서", source_url="https://example.test/a",
        published_at=datetime(2026, 3, 1),
    )
    second = service.extract_and_persist(
        db, source.id, "베타전자와 신규 공급계약을 체결했습니다.",
        source_document="공급계약 공시", source_url="https://example.test/b",
    )
    assert first == {"relationships": 1, "evidences": 1, "skipped": 0}
    assert second == {"relationships": 0, "evidences": 1, "skipped": 0}
    assert db.query(Relationship).count() == 1
    assert db.query(RelationshipEvidence).count() == 2
    assert db.query(Relationship).one().status == "proposed"


def test_ambiguous_company_name_and_empty_evidence_are_rejected():
    db = Session()
    source = Company(name="소스")
    db.add_all([source, Company(name="동명기업"), Company(name="동명기업")])
    db.commit()
    service = RelationshipExtractionService()
    result = service.persist_candidates(db, source.id, [
        RelationshipCandidate("동명기업", "SUPPLIES_TO", "동명기업에 공급", confidence=0.9),
        RelationshipCandidate("없는기업", "SUPPLIES_TO", "", confidence=0.9),
    ], "문서")
    assert result == {"relationships": 0, "evidences": 0, "skipped": 2}


def test_llm_candidate_stays_proposed_and_review_can_be_audited():
    db = Session()
    source, target = Company(name="소스"), Company(name="타깃")
    db.add_all([source, target])
    db.commit()
    service = RelationshipExtractionService()
    service.persist_candidates(db, source.id, [
        RelationshipCandidate("타깃", "PARTNERS_WITH", "타깃과 공동개발한다.", confidence=0.99),
    ], "LLM 입력 문서", extractor_version="llm_v1")
    edge = db.query(Relationship).one()
    assert edge.status == "proposed"
    assert edge.extractor_version == "llm_v1"


def test_goldset_metrics():
    predicted = {(1, 2, "SUPPLIES_TO"), (1, 3, "PARTNERS_WITH")}
    gold = {(1, 2, "SUPPLIES_TO"), (2, 3, "OWNS")}
    report = RelationshipExtractionService.evaluate(predicted, gold)
    assert report == {
        "true_positive": 1, "predicted": 2, "gold": 2,
        "precision": 0.5, "recall": 0.5, "f1": 0.5,
    }


def test_relationship_review_routes_are_registered():
    paths = app.openapi()["paths"]
    assert "/api/v1/relationships/extract" in paths
    assert "/api/v1/relationships/candidates" in paths
    assert "/api/v1/relationships/review-queue" in paths
    assert "/api/v1/relationships/{relationship_id}/review" in paths
