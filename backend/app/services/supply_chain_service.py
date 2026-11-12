"""Alpha Chain — Supply Chain Normalization & Upstream Tier Calculation Service (S2-B3)

Normalizes company relationships into directed SUPPLIER -> CUSTOMER edges,
validates relationship verification status, validity periods, and evidence timestamps,
and computes upstream supply chain tiers (Tier 1, 2, 3) from anchor companies.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from typing import Dict, Hashable, Iterable, List, Optional, Set, Tuple, Union

from sqlalchemy.orm import Session, selectinload

from app.models.schema import Relationship, RelationshipEvidence

VALID_SUPPLY_TYPES: Set[str] = {"SUPPLIES_TO", "CUSTOMER_OF"}
EXCLUDED_RELATIONSHIP_TYPES: Set[str] = {
    "PARTNERS_WITH",
    "COMPETES_WITH",
    "OWNS",
    "BELONGS_TO",
}
MAX_SUPPLY_HOPS: int = 3


def to_utc_datetime(
    dt_val: Optional[Union[datetime, date]], is_end_of_day: bool = False
) -> Optional[datetime]:
    """Convert a date or datetime to UTC timezone-aware datetime."""
    if dt_val is None:
        return None
    if isinstance(dt_val, datetime):
        if dt_val.tzinfo is None:
            return dt_val.replace(tzinfo=timezone.utc)
        return dt_val.astimezone(timezone.utc)
    if isinstance(dt_val, date):
        t = time.max if is_end_of_day else time.min
        return datetime.combine(dt_val, t).replace(tzinfo=timezone.utc)
    return None


@dataclass
class NormalizedSupplyEdge:
    supplier_id: int
    customer_id: int
    relationship_id: Hashable
    original_type: str
    status: str
    confidence: float
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None
    evidences: List[RelationshipEvidence] = field(default_factory=list)
    latest_evidence_published_at: Optional[datetime] = None
    source_relationship_ids: List[Hashable] = field(default_factory=list)

    @property
    def key(self) -> Tuple[int, int]:
        return (self.supplier_id, self.customer_id)


@dataclass
class SupplyChainPath:
    supplier_id: int
    anchor_id: int
    tier: int
    hops: int
    node_path: List[int]  # [supplier_id, ..., anchor_id]
    edge_path: List[NormalizedSupplyEdge]


@dataclass
class SupplyChainResult:
    anchor_id: int
    as_of: datetime
    tier1_suppliers: List[int]
    tier2_suppliers: List[int]
    tier3_suppliers: List[int]
    all_suppliers: Dict[int, int]  # supplier_id -> minimum tier
    paths: List[SupplyChainPath]
    normalized_edges: List[NormalizedSupplyEdge]

    def get_paths_for_supplier(self, supplier_id: int) -> List[SupplyChainPath]:
        return [p for p in self.paths if p.supplier_id == supplier_id]

    def get_suppliers_at_tier(self, tier: int, exclusive: bool = True) -> List[int]:
        if exclusive:
            return [s for s, t in self.all_suppliers.items() if t == tier]
        return sorted({p.supplier_id for p in self.paths if p.tier == tier})


class SupplyChainService:
    def __init__(self, max_hops: int = MAX_SUPPLY_HOPS):
        self.max_hops = max_hops

    def normalize_relationship(
        self,
        rel: Relationship,
        as_of: Union[datetime, date],
        conservative_date_only: bool = True,
    ) -> Optional[NormalizedSupplyEdge]:
        """Validates and normalizes a single Relationship into a SUPPLIER -> CUSTOMER edge.

        Returns None if relation is not a valid, verified, active supply relation as of as_of.
        """
        # 1. Type validation (only SUPPLIES_TO and CUSTOMER_OF allowed)
        if rel.type not in VALID_SUPPLY_TYPES:
            return None

        # 2. Status validation (verified only; proposed/rejected/None excluded)
        if rel.status != "verified":
            return None

        # 3. Direction normalization
        if rel.type == "SUPPLIES_TO":
            supplier_id = rel.source_id
            customer_id = rel.target_id
        elif rel.type == "CUSTOMER_OF":
            supplier_id = rel.target_id
            customer_id = rel.source_id
        else:
            return None

        # Self-loops are invalid
        if supplier_id == customer_id:
            return None

        # 4. Validity period check against as_of date
        as_of_utc = to_utc_datetime(
            as_of,
            is_end_of_day=isinstance(as_of, date) and not isinstance(as_of, datetime),
        )
        if as_of_utc is None:
            return None
        as_of_date = as_of_utc.date()

        if rel.valid_from is not None and rel.valid_from > as_of_date:
            return None
        if rel.valid_to is not None and rel.valid_to < as_of_date:
            return None

        # 5. Evidence check: must have at least 1 valid evidence published at or before as_of
        evidences = getattr(rel, "evidences", None) or []
        valid_evidences: List[RelationshipEvidence] = []

        for ev in evidences:
            # Relationship approval never implicitly approves individual evidence.
            if ev.verification_status != "verified":
                continue
            if ev.published_at is None:
                continue
            ev_pub_utc = to_utc_datetime(ev.published_at)
            if ev_pub_utc is None:
                continue

            # Conservative check for date-only evidence (time component is 00:00:00)
            if conservative_date_only and (
                ev.published_at.hour == 0
                and ev.published_at.minute == 0
                and ev.published_at.second == 0
                and ev.published_at.microsecond == 0
            ):
                # If as_of is strictly on a later day, evidence is definitely published
                if as_of_date > ev.published_at.date():
                    valid_evidences.append(ev)
                elif as_of_date == ev.published_at.date():
                    # Available at end of that date
                    ev_available_utc = datetime.combine(
                        ev.published_at.date(), time.max
                    ).replace(tzinfo=timezone.utc)
                    if as_of_utc >= ev_available_utc:
                        valid_evidences.append(ev)
                # If as_of is on the same day before end of day, conservative treatment excludes it
            else:
                if ev_pub_utc <= as_of_utc:
                    valid_evidences.append(ev)

        if not valid_evidences:
            return None

        latest_pub = max(to_utc_datetime(ev.published_at) for ev in valid_evidences)

        return NormalizedSupplyEdge(
            supplier_id=supplier_id,
            customer_id=customer_id,
            relationship_id=rel.id,
            original_type=rel.type,
            status=rel.status,
            confidence=float(rel.confidence) if rel.confidence is not None else 0.0,
            valid_from=rel.valid_from,
            valid_to=rel.valid_to,
            evidences=valid_evidences,
            latest_evidence_published_at=latest_pub,
            source_relationship_ids=[rel.id],
        )

    def deduplicate_edges(
        self, edges: Iterable[NormalizedSupplyEdge]
    ) -> List[NormalizedSupplyEdge]:
        """Deduplicates multiple edges between the same (supplier_id, customer_id).

        Chooses the edge with highest confidence, combines evidences and source IDs.
        """
        grouped: Dict[Tuple[int, int], List[NormalizedSupplyEdge]] = defaultdict(list)
        for edge in edges:
            grouped[edge.key].append(edge)

        deduped: List[NormalizedSupplyEdge] = []
        for (sup_id, cust_id), edge_list in grouped.items():
            if len(edge_list) == 1:
                deduped.append(edge_list[0])
                continue

            # Sort by confidence descending, then latest published_at descending
            edge_list.sort(
                key=lambda e:
                    (
                        e.confidence,
                        e.latest_evidence_published_at or datetime.min.replace(tzinfo=timezone.utc),
                    ),
                reverse=True,
            )
            best = edge_list[0]

            # Merge evidences and source relationship IDs
            all_evidences = []
            seen_ev_ids = set()
            all_rel_ids = []
            for e in edge_list:
                all_rel_ids.extend(e.source_relationship_ids)
                for ev in e.evidences:
                    ev_id = getattr(ev, "id", None)
                    if ev_id is not None:
                        if ev_id not in seen_ev_ids:
                            seen_ev_ids.add(ev_id)
                            all_evidences.append(ev)
                    else:
                        all_evidences.append(ev)

            merged_edge = NormalizedSupplyEdge(
                supplier_id=sup_id,
                customer_id=cust_id,
                relationship_id=best.relationship_id,
                original_type=best.original_type,
                status=best.status,
                confidence=best.confidence,
                valid_from=best.valid_from,
                valid_to=best.valid_to,
                evidences=all_evidences,
                latest_evidence_published_at=best.latest_evidence_published_at,
                source_relationship_ids=list(dict.fromkeys(all_rel_ids)),
            )
            deduped.append(merged_edge)

        return deduped

    def find_supply_chain(
        self,
        db: Session,
        anchor_company_id: int,
        as_of: Optional[Union[datetime, date]] = None,
        max_hops: Optional[int] = None,
        conservative_date_only: bool = True,
    ) -> SupplyChainResult:
        """Explores upstream supply chain up to max_hops from anchor_company_id.

        Preserves alternative paths while detecting and breaking cycles.
        """
        hops_limit = max_hops if max_hops is not None else self.max_hops
        as_of_dt = (
            to_utc_datetime(
                as_of,
                is_end_of_day=isinstance(as_of, date) and not isinstance(as_of, datetime),
            )
            if as_of is not None
            else datetime.now(timezone.utc)
        )

        # 1. Query relevant relationships
        query = (
            db.query(Relationship)
            .options(selectinload(Relationship.evidences))
            .filter(
                Relationship.type.in_(VALID_SUPPLY_TYPES),
                Relationship.status == "verified",
            )
        )
        raw_relationships = query.all()

        # 2. Normalize and filter
        normalized_edges: List[NormalizedSupplyEdge] = []
        for rel in raw_relationships:
            norm = self.normalize_relationship(
                rel, as_of_dt, conservative_date_only=conservative_date_only
            )
            if norm is not None:
                normalized_edges.append(norm)

        return self.find_supply_chain_from_edges(
            anchor_company_id,
            normalized_edges,
            as_of=as_of_dt,
            max_hops=hops_limit,
        )

    def find_supply_chain_from_edges(
        self,
        anchor_company_id: int,
        edges: Iterable[NormalizedSupplyEdge],
        *,
        as_of: Union[datetime, date],
        max_hops: Optional[int] = None,
    ) -> SupplyChainResult:
        """Explore upstream from explicit, already-verified normalized edges.

        This is the non-ORM input seam for historical verification snapshots. It
        intentionally performs no status/evidence inference; callers must supply
        only eligible SUPPLIES_TO edges for the same ``as_of``.
        """
        hops_limit = max_hops if max_hops is not None else self.max_hops
        if type(hops_limit) is not int or not 1 <= hops_limit <= MAX_SUPPLY_HOPS:
            raise ValueError("max_hops must be an integer from 1 to 3")
        as_of_dt = to_utc_datetime(as_of)
        if as_of_dt is None:
            raise ValueError("as_of must be a date or datetime")
        supplied_edges = list(edges)
        if any(
            edge.original_type not in VALID_SUPPLY_TYPES
            or edge.status != "verified"
            or edge.supplier_id == edge.customer_id
            for edge in supplied_edges
        ):
            raise ValueError("explicit edges must be verified normalized supply relations")

        # 3. Deduplicate
        deduped_edges = self.deduplicate_edges(supplied_edges)

        # 4. Build upstream adjacency map: customer_id -> list of incoming edges (suppliers)
        customer_to_suppliers: Dict[int, List[NormalizedSupplyEdge]] = defaultdict(list)
        for edge in deduped_edges:
            customer_to_suppliers[edge.customer_id].append(edge)

        # 5. Upstream traversal (BFS with path-level visited to prevent cycles while preserving alternative paths)
        queue: deque[Tuple[int, List[int], List[NormalizedSupplyEdge]]] = deque([
            (anchor_company_id, [anchor_company_id], [])
        ])

        found_paths: List[SupplyChainPath] = []

        while queue:
            curr_node, node_path, edge_path = queue.popleft()

            if len(edge_path) >= hops_limit:
                continue

            for edge in customer_to_suppliers.get(curr_node, []):
                next_sup_id = edge.supplier_id

                # Cycle prevention: if next_sup_id is already in current path, skip!
                if next_sup_id in node_path:
                    continue

                new_node_path = [next_sup_id] + node_path
                new_edge_path = [edge] + edge_path
                current_tier = len(new_edge_path)

                path_obj = SupplyChainPath(
                    supplier_id=next_sup_id,
                    anchor_id=anchor_company_id,
                    tier=current_tier,
                    hops=current_tier,
                    node_path=new_node_path,
                    edge_path=new_edge_path,
                )
                found_paths.append(path_obj)

                if current_tier < hops_limit:
                    queue.append((next_sup_id, new_node_path, new_edge_path))

        # 6. Compute tier classifications
        all_suppliers: Dict[int, int] = {}
        for p in found_paths:
            if p.supplier_id not in all_suppliers or p.tier < all_suppliers[p.supplier_id]:
                all_suppliers[p.supplier_id] = p.tier

        tier1 = sorted([s for s, t in all_suppliers.items() if t == 1])
        tier2 = sorted([s for s, t in all_suppliers.items() if t == 2])
        tier3 = sorted([s for s, t in all_suppliers.items() if t == 3])

        return SupplyChainResult(
            anchor_id=anchor_company_id,
            as_of=as_of_dt,
            tier1_suppliers=tier1,
            tier2_suppliers=tier2,
            tier3_suppliers=tier3,
            all_suppliers=all_suppliers,
            paths=found_paths,
            normalized_edges=deduped_edges,
        )
