import hashlib
import time
from datetime import date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.main import app
from app.models.schema import Company, Relationship, RelationshipEvidence
from app.services.graph_service import GraphSearchService, edge_score, path_score

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Session = sessionmaker(bind=engine)


@pytest.fixture(autouse=True)
def database():
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


def add_edge(db, source, target, kind="SUPPLIES_TO", confidence=0.9,
             status="verified", valid_from=None, valid_to=None, published_at=None):
    edge = Relationship(
        source_id=source.id, target_id=target.id, type=kind, status=status,
        confidence=confidence, valid_from=valid_from, valid_to=valid_to,
    )
    db.add(edge)
    db.flush()
    published_at = published_at or datetime(2025, 1, 1)
    digest = hashlib.sha256(f"{edge.id}-{published_at}".encode()).hexdigest()
    db.add(RelationshipEvidence(
        relationship_id=edge.id, excerpt=f"{source.name}에서 {target.name}으로 연결",
        published_at=published_at, source_document="검증 문서", confidence=confidence,
        evidence_hash=digest,
    ))
    db.flush()
    return edge


def graph_fixture(db):
    companies = [Company(name=name) for name in "ABCDE"]
    db.add_all(companies)
    db.flush()
    a, b, c, d, e = companies
    ab = add_edge(db, a, b, confidence=0.95)
    bc = add_edge(db, b, c, confidence=0.90)
    add_edge(db, c, a, confidence=0.85)
    add_edge(db, a, c, confidence=0.60)
    add_edge(db, c, d, confidence=0.95, valid_to=date(2024, 12, 31))
    add_edge(db, a, e, confidence=0.99, published_at=datetime(2026, 1, 1))
    db.commit()
    return companies, ab, bc


def test_direction_is_preserved_and_cycle_is_blocked():
    db = Session()
    (a, b, c, _, _), ab, bc = graph_fixture(db)
    service = GraphSearchService()
    downstream = service.search(db, a.id, "downstream", 3, as_of=date(2025, 6, 1))
    assert [path["target_id"] for path in downstream["paths"]] == [b.id, c.id]
    assert len({path["target_id"] for path in downstream["paths"]}) == 2
    assert all(path["target_id"] != a.id for path in downstream["paths"])
    c_path = next(path for path in downstream["paths"] if path["target_id"] == c.id)
    assert c_path["edge_ids"] == [ab.id, bc.id]
    assert c_path["traversals"] == ["downstream", "downstream"]

    upstream = service.search(db, b.id, "upstream", 1, as_of=date(2025, 6, 1))
    assert upstream["paths"][0]["target_id"] == a.id
    assert upstream["paths"][0]["traversals"] == ["upstream"]
    edge = next(item for item in upstream["graph_edges"] if item["id"] == ab.id)
    assert edge["source_id"] == a.id and edge["target_id"] == b.id


def test_as_of_status_type_confidence_and_evidence_filters():
    db = Session()
    (a, b, c, d, e), _, _ = graph_fixture(db)
    proposed = add_edge(db, a, d, status="proposed")
    db.commit()
    result = GraphSearchService().search(
        db, a.id, "downstream", 3, {"SUPPLIES_TO"}, 0.7,
        date(2025, 6, 1),
    )
    ids = {path["target_id"] for path in result["paths"]}
    assert ids == {b.id, c.id}
    assert d.id not in ids and e.id not in ids
    assert proposed.id not in {edge["id"] for edge in result["graph_edges"]}


def test_pagination_and_scoring():
    db = Session()
    (a, _, _, _, _), _, _ = graph_fixture(db)
    first = GraphSearchService().search(db, a.id, "downstream", 3, as_of=date(2025, 6, 1), limit=1)
    assert first["pagination"] == {"offset": 0, "limit": 1, "total": 2, "has_more": True}
    evidence = db.query(RelationshipEvidence).first()
    assert 0 < edge_score(0.9, [evidence], date(2025, 6, 1)) <= 1
    assert path_score([0.9, 0.8]) < path_score([0.9])


def test_graph_search_performance_for_large_branching_graph():
    db = Session()
    companies = [Company(name=f"기업{i}") for i in range(501)]
    db.add_all(companies)
    db.flush()
    for index in range(1, 501):
        parent = companies[(index - 1) // 10]
        add_edge(db, parent, companies[index], confidence=0.8)
    db.commit()
    started = time.perf_counter()
    result = GraphSearchService().search(db, companies[0].id, "downstream", 3, limit=200)
    elapsed = time.perf_counter() - started
    assert result["pagination"]["total"] > 100
    assert elapsed < 1.0


def test_graph_api_route_is_registered():
    assert "/api/v1/companies/{company_id}/graph" in app.openapi()["paths"]
