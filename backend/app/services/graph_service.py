import math
from collections import defaultdict, deque
from datetime import date, datetime, time
from typing import Dict, Iterable, Optional, Set, Tuple

from sqlalchemy.orm import Session, selectinload

from app.models.schema import Company, Relationship, RelationshipEvidence
from app.services.relationship_service import RELATIONSHIP_TYPES


def edge_score(confidence: float, evidences: Iterable[RelationshipEvidence], as_of: date) -> float:
    rows = list(evidences)
    if not rows:
        return 0.0
    latest = max(row.published_at.date() for row in rows)
    age_days = max(0, (as_of - latest).days)
    recency = max(0.0, 1.0 - age_days / 1095.0)
    evidence_strength = min(len(rows), 5) / 5.0
    return round(0.7 * confidence + 0.2 * recency + 0.1 * evidence_strength, 6)


def path_score(scores: Iterable[float]) -> float:
    values = list(scores)
    if not values or any(value <= 0 for value in values):
        return 0.0
    geometric_mean = math.prod(values) ** (1 / len(values))
    return round(geometric_mean * (0.95 ** (len(values) - 1)), 6)


class GraphSearchService:
    def search(
        self, db: Session, company_id: int, direction: str = "both", hops: int = 3,
        relationship_types: Optional[Set[str]] = None, min_confidence: float = 0.0,
        as_of: Optional[date] = None, limit: int = 50, offset: int = 0,
    ) -> Dict:
        if direction not in {"upstream", "downstream", "both"}:
            raise ValueError("direction must be upstream, downstream, or both")
        if hops < 1 or hops > 3:
            raise ValueError("hops must be between 1 and 3")
        if relationship_types and not relationship_types <= RELATIONSHIP_TYPES:
            raise ValueError("unsupported relationship type")
        start = db.get(Company, company_id)
        if start is None:
            raise LookupError("company not found")
        as_of = as_of or date.today()
        cutoff = datetime.combine(as_of, time.max)
        query = db.query(Relationship).options(selectinload(Relationship.evidences)).filter(
            Relationship.status == "verified",
            Relationship.confidence >= min_confidence,
        ).filter(
            (Relationship.valid_from.is_(None)) | (Relationship.valid_from <= as_of),
            (Relationship.valid_to.is_(None)) | (Relationship.valid_to >= as_of),
        )
        if relationship_types:
            query = query.filter(Relationship.type.in_(relationship_types))

        edge_data = {}
        adjacency = defaultdict(list)
        for edge in query.all():
            evidences = [ev for ev in edge.evidences if ev.published_at <= cutoff]
            if not evidences:
                continue
            score = edge_score(edge.confidence, evidences, as_of)
            edge_data[edge.id] = (edge, evidences, score)
            if direction in {"downstream", "both"}:
                adjacency[edge.source_id].append((edge.target_id, edge.id, "downstream"))
            if direction in {"upstream", "both"}:
                adjacency[edge.target_id].append((edge.source_id, edge.id, "upstream"))

        queue = deque([(company_id, tuple(), frozenset({company_id}), tuple())])
        best: Dict[int, Tuple[float, Tuple[int, ...], Tuple[str, ...]]] = {}
        while queue:
            node_id, edge_ids, visited, traversals = queue.popleft()
            if len(edge_ids) >= hops:
                continue
            for next_id, edge_id, traversal in adjacency[node_id]:
                if next_id in visited:
                    continue
                next_edges = edge_ids + (edge_id,)
                next_traversals = traversals + (traversal,)
                score = path_score(edge_data[item][2] for item in next_edges)
                current = best.get(next_id)
                if current is None or score > current[0] or (
                    score == current[0] and len(next_edges) < len(current[1])
                ):
                    best[next_id] = (score, next_edges, next_traversals)
                queue.append((next_id, next_edges, visited | {next_id}, next_traversals))

        ranked = sorted(best.items(), key=lambda item: (-item[1][0], item[0]))
        page = ranked[offset:offset + limit]
        selected_edge_ids = {edge_id for _, (_, path, _) in page for edge_id in path}
        selected_node_ids = {company_id, *(node_id for node_id, _ in page)}
        for edge_id in selected_edge_ids:
            edge = edge_data[edge_id][0]
            selected_node_ids.update((edge.source_id, edge.target_id))
        companies = {
            row.id: row for row in db.query(Company).filter(Company.id.in_(selected_node_ids)).all()
        }

        graph_edges = []
        for edge_id in sorted(selected_edge_ids):
            edge, evidences, score = edge_data[edge_id]
            graph_edges.append({
                "id": edge.id, "source_id": edge.source_id, "target_id": edge.target_id,
                "type": edge.type, "confidence": edge.confidence, "score": score,
                "explanation": f"{companies[edge.source_id].name} --{edge.type}--> {companies[edge.target_id].name}",
                "evidences": [{
                    "id": ev.id, "text": ev.excerpt, "location": ev.location,
                    "source_document": ev.source_document, "source_url": ev.source_url,
                    "published_at": ev.published_at,
                } for ev in sorted(evidences, key=lambda row: row.published_at, reverse=True)],
            })
        paths = [{
            "target_id": node_id, "score": score, "hops": len(edge_ids),
            "edge_ids": list(edge_ids), "traversals": list(traversals),
            "explanation": " -> ".join(
                edge_data[edge_id][0].type for edge_id in edge_ids
            ),
        } for node_id, (score, edge_ids, traversals) in page]
        return {
            "start_company_id": company_id, "direction": direction, "as_of": as_of,
            "graph_nodes": [{"id": item, "name": companies[item].name}
                            for item in sorted(selected_node_ids)],
            "graph_edges": graph_edges, "paths": paths,
            "pagination": {"offset": offset, "limit": limit, "total": len(ranked),
                           "has_more": offset + limit < len(ranked)},
        }
