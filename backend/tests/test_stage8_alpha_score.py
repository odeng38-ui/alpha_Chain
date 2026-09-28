import hashlib
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.main import app
from app.models.schema import (
    Company,
    DailyPrice,
    DisclosureEvent,
    FeatureSnapshot,
    Filing,
    FinancialFact,
    MacroObservation,
    Relationship,
    RelationshipEvidence,
    ScoreSnapshot,
    Security,
)
from app.services.alpha_score_service import COMPONENTS, AlphaScoreService

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Session = sessionmaker(bind=engine)


@pytest.fixture(autouse=True)
def database():
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


def seed_complete_inputs(db):
    company = Company(name="점수기업", industry_id="반도체")
    peer_company = Company(name="동종기업", industry_id="반도체")
    partner = Company(name="고객기업")
    db.add_all([company, peer_company, partner])
    db.flush()
    security = Security(company_id=company.id, market="KOSPI", ticker="000001", security_type="COMMON")
    peer = Security(company_id=peer_company.id, market="KOSPI", ticker="000002", security_type="COMMON")
    db.add_all([security, peer])
    db.flush()
    as_of = date(2025, 6, 30)
    for index in range(70):
        trade_date = as_of - timedelta(days=index)
        db.add(DailyPrice(security_id=security.id, trade_date=trade_date, close=Decimal(str(100 - index * 0.5))))
        db.add(DailyPrice(security_id=peer.id, trade_date=trade_date, close=Decimal(str(100 - index * 0.2))))
    db.add(DailyPrice(security_id=security.id, trade_date=date(2025, 7, 1), close=Decimal("10000")))

    for series_id, value in (("UNRATE", "4.0"), ("T10Y2Y", "1.0"), ("FEDFUNDS", "4.0")):
        db.add(MacroObservation(
            series_id=series_id, observation_date=date(2025, 5, 1), vintage_date=date(2025, 6, 1),
            available_at=datetime(2025, 6, 1), value=Decimal(value), collected_at=datetime(2025, 6, 2),
        ))
    db.add(MacroObservation(
        series_id="UNRATE", observation_date=date(2025, 7, 1), vintage_date=date(2025, 7, 5),
        available_at=datetime(2025, 7, 5), value=Decimal("9"), collected_at=datetime(2025, 7, 5),
    ))

    facts = (("Revenue", "매출액", "1000"), ("OperatingIncomeLoss", "영업이익", "150"),
             ("Assets", "자산총계", "2000"), ("Liabilities", "부채총계", "800"))
    for index, (account_id, account_name, value) in enumerate(facts):
        db.add(FinancialFact(
            company_id=company.id, business_year="2024", report_code="11011", fs_div="CFS",
            row_key=str(index), period="2024", account_id=account_id, account_name=account_name,
            value=Decimal(value), consolidated=True, filed_at=datetime(2025, 3, 31),
        ))
    filing = Filing(
        rcept_no="20250601000001", company_id=company.id, title="공급계약",
        filed_at=datetime(2025, 6, 1), available_at=datetime(2025, 6, 1),
    )
    db.add(filing)
    db.add(DisclosureEvent(
        filing_id=filing.rcept_no, company_id=company.id, event_type="SUPPLY_CONTRACT",
        sentiment="POSITIVE", event_date=datetime(2025, 6, 1),
    ))
    edge = Relationship(
        source_id=company.id, target_id=partner.id, type="SUPPLIES_TO",
        status="verified", confidence=0.9,
    )
    db.add(edge)
    db.flush()
    db.add(RelationshipEvidence(
        relationship_id=edge.id, excerpt="고객기업에 공급", published_at=datetime(2025, 5, 1),
        source_document="공시", confidence=0.9,
        evidence_hash=hashlib.sha256(b"score-evidence").hexdigest(),
    ))
    db.commit()
    return security, as_of


def test_score_is_reproducible_traceable_and_matches_manual_weighting():
    db = Session()
    security, as_of = seed_complete_inputs(db)
    service = AlphaScoreService()
    first = service.score(db, security.id, as_of)
    second = service.score(db, security.id, as_of)
    assert first.id == second.id
    assert db.query(ScoreSnapshot).count() == 1
    assert db.query(FeatureSnapshot).count() == 6
    assert first.completeness == 1.0
    expected = sum(first.component[name] * first.weights[name] for name in COMPONENTS)
    assert first.total == pytest.approx(expected)
    assert first.confidence != first.total
    assert first.config_hash and len(first.config_hash) == 64
    for feature in db.query(FeatureSnapshot).all():
        assert feature.formula
        assert feature.inputs is not None
        assert feature.missing_reason is None
        assert feature.source_available_at.date() <= as_of


def test_future_inputs_are_not_used():
    db = Session()
    security, as_of = seed_complete_inputs(db)
    row = AlphaScoreService().score(db, security.id, as_of)
    momentum = db.query(FeatureSnapshot).filter_by(feature_name="Momentum").one()
    macro = db.query(FeatureSnapshot).filter_by(feature_name="Macro").one()
    assert momentum.inputs["latest_trade_date"] == as_of.isoformat()
    assert macro.inputs["UNRATE"] == 4.0
    assert row.as_of_date == as_of


def test_missing_components_are_explicit_not_neutral():
    db = Session()
    company = Company(name="결측기업")
    db.add(company)
    db.flush()
    security = Security(company_id=company.id, market="KOSPI", ticker="999999", security_type="COMMON")
    db.add(security)
    db.flush()
    for index in range(30):
        db.add(DailyPrice(
            security_id=security.id, trade_date=date(2025, 6, 30) - timedelta(days=index),
            close=Decimal(str(100 + index)),
        ))
    db.commit()
    row = AlphaScoreService().score(db, security.id, date(2025, 6, 30))
    assert row.component["Fundamental"] is None
    assert row.component["Industry"] is None
    assert row.component["Momentum"] is not None
    assert row.completeness < 1
    missing = {item["component"] for item in row.explanations["missing_components"]}
    assert {"Fundamental", "Industry", "Chain"} <= missing


def test_weight_changes_are_versioned_and_normalized():
    db = Session()
    weights = {name: 1 for name in COMPONENTS}
    row = AlphaScoreService.save_weights(db, "v1.1", weights, "admin", "equal weight test")
    assert sum(row.weights.values()) == pytest.approx(1.0)
    assert row.changed_by == "admin"
    with pytest.raises(ValueError):
        AlphaScoreService.save_weights(db, "v1.1", weights, "admin", "duplicate")


def test_score_api_routes_are_registered():
    paths = app.openapi()["paths"]
    assert "/api/v1/scores/batch" in paths
    assert "/api/v1/scores/security/{security_id}" in paths
    assert "/api/v1/scores/config/history" in paths
