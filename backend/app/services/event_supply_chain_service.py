"""S2-B4: read-only event -> verified upstream paths -> Korean listed candidates.

No network, commits, persistence, score or economic-benefit inference. Naive stored
DB timestamps follow the existing UTC convention; request as_of must be aware.
Verification is the stored review state, not a reconstruction of review history.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy.orm import Session, selectinload

from app.models.schema import Company, GlobalEvent, IdentifierMap, Relationship
from app.services.event_evidence_service import (
    EventCompanyLinkService,
    GlobalEventEvidenceService,
)
from app.services.identifier_service import (
    EXCLUSIVE_IDENTIFIER_SOURCES,
    InvalidIdentifierError,
    normalize_identifier,
)
from app.services.supply_chain_service import (
    VALID_SUPPLY_TYPES,
    SupplyChainService,
    to_utc_datetime,
)


def _stamp(value: datetime | None) -> str | None:
    return to_utc_datetime(value).isoformat() if value is not None else None


def _within(day: date, start: date | None, end: date | None) -> bool:
    return (start is None or start <= day) and (end is None or day <= end)


def _company(company: Company) -> dict[str, Any]:
    return {
        "company_id": company.id,
        "name": company.name,
        "country_code": company.country_code,
        "verification_status": company.verification_status,
    }


def _evidence(row: Any) -> dict[str, Any]:
    result = {
        key: getattr(row, key)
        for key in (
            "id",
            "source_document",
            "source_url",
            "location",
            "excerpt",
            "verification_status",
        )
    }
    result["published_at"] = _stamp(row.published_at)
    if hasattr(row, "evidence_type"):
        result.update(
            evidence_type=row.evidence_type,
            collected_at=_stamp(row.collected_at),
            extracted_facts=row.extracted_facts or {},
        )
    else:
        result.update(relationship_id=row.relationship_id, confidence=row.confidence)
    return result


class EventSupplyChainService:
    """Compose B2/B3 without changing models or their existing interfaces."""

    def __init__(self) -> None:
        self.supply_chain = SupplyChainService()

    def analyze(
        self, db: Session, *, event_id: int, as_of: datetime, max_supply_tier: int = 3
    ) -> dict[str, Any]:
        if not isinstance(as_of, datetime) or as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be an explicit timezone-aware datetime")
        if type(max_supply_tier) is not int or not 1 <= max_supply_tier <= 3:
            raise ValueError("max_supply_tier must be an integer from 1 to 3")
        as_of = as_of.astimezone(timezone.utc)
        # Queries must not flush unrelated caller changes.
        with db.no_autoflush:
            return self._analyze(db, event_id, as_of, max_supply_tier)

    def _analyze(
        self, db: Session, event_id: int, as_of: datetime, max_tier: int
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "event": None,
            "as_of": _stamp(as_of),
            "max_supply_tier": max_tier,
            "anchors": [],
            "candidates": [],
            "event_evidence": [],
            "interpretations": [],
            "excluded_companies": [],
            "excluded_relationships": [],
            "empty_reasons": [],
            "uncertainties": [
                "Supply-chain connectivity does not establish incremental revenue or economic benefit.",
                "Stored verification states have no historical verified_at timeline; historical review state cannot be reconstructed.",
                "Identifier maps have no independent review status; verified company and anchor link plus conflict-free temporal mappings are required.",
            ],
        }
        event = db.get(GlobalEvent, event_id)

        def empty(reason: str) -> dict[str, Any]:
            result["empty_reasons"].append(reason)
            return result

        if event is None:
            return empty("event_not_found")
        result["event"] = {
            "event_id": event.id,
            "title": event.title,
            "event_kind": event.event_kind,
            "origin_country": event.origin_country,
            "occurred_at": _stamp(event.occurred_at),
            "available_at": _stamp(event.available_at),
            "verification_status": event.verification_status,
        }
        if event.verification_status != "verified":
            return empty("event_not_verified")
        if event.origin_country != "US":
            return empty("event_not_us")
        if event.available_at is None or to_utc_datetime(event.available_at) > as_of:
            return empty("event_not_available_as_of")
        if event.collected_at is not None and to_utc_datetime(event.collected_at) > as_of:
            return empty("event_not_collected_as_of")

        evidences = sorted(
            (
                ev
                for ev in GlobalEventEvidenceService.get_evidence_before(db, event_id, as_of)
                if ev.verification_status == "verified"
                and ev.evidence_type in {"OFFICIAL", "REGULATORY"}
                and ev.collected_at is not None
                and to_utc_datetime(ev.collected_at) <= as_of
                and (ev.source_document or ev.source_url)
                and ev.excerpt
            ),
            key=lambda ev: ev.id,
        )
        result["event_evidence"] = [_evidence(ev) for ev in evidences]
        result["interpretations"] = [
            {"evidence_id": ev.id, "interpretations": ev.interpretations}
            for ev in evidences
            if ev.interpretations
        ]
        if not evidences:
            return empty("no_verified_official_evidence_available_as_of")
        eligible_evidence_ids = {ev.id for ev in evidences}
        day = as_of.date()
        identifiers = (
            db.query(IdentifierMap)
            .filter(IdentifierMap.source.in_(EXCLUSIVE_IDENTIFIER_SOURCES))
            .order_by(IdentifierMap.id)
            .all()
        )
        active = [
            row
            for row in identifiers
            if row.effective_from is not None and _within(day, row.effective_from, row.effective_to)
        ]
        # Normalize using B1, including legacy unpadded values, before detecting collisions.
        identity: dict[tuple[str, str], set[int | None]] = {}
        company_maps: dict[int, list[dict[str, Any]]] = {}
        invalid_company_ids = set()
        for row in active:
            try:
                value = normalize_identifier(row.source, row.source_id_value)
            except InvalidIdentifierError:
                invalid_company_ids.add(row.company_id)
                continue
            identity.setdefault((row.source, value), set()).add(row.company_id)
            company_maps.setdefault(row.company_id, []).append(
                {
                    "identifier_map_id": row.id,
                    "source": row.source,
                    "value": value,
                    "effective_from": row.effective_from.isoformat(),
                    "effective_to": row.effective_to.isoformat() if row.effective_to else None,
                }
            )
        links = sorted(
            EventCompanyLinkService.get_verified_anchors(db, event_id, as_of),
            key=lambda link: (link.company_id, link.id),
        )
        candidates: dict[int, dict[str, Any]] = {}
        for link in links:
            company = db.get(Company, link.company_id)
            maps = company_maps.get(link.company_id, [])
            reason = None
            if link.evidence_id not in eligible_evidence_ids:
                reason = "anchor_evidence_not_eligible"
            elif not _within(day, link.valid_from, link.valid_to):
                reason = "anchor_link_not_effective_as_of"
            elif company is None or company.country_code != "US":
                reason = "anchor_not_us_company"
            elif company.verification_status != "verified":
                reason = "anchor_company_not_verified"
            elif company.id in invalid_company_ids:
                reason = "anchor_identifier_invalid"
            elif not maps:
                reason = "anchor_identifier_missing_or_not_effective_as_of"
            elif any(len(identity[(m["source"], m["value"])]) != 1 for m in maps):
                reason = "anchor_identifier_conflict"
            if reason:
                result["excluded_companies"].append(
                    {"company_id": link.company_id, "role": "anchor", "reason": reason}
                )
                continue
            result["anchors"].append(
                {
                    **_company(company),
                    "identifiers": maps,
                    "link_id": link.id,
                    "evidence_id": link.evidence_id,
                    "verification_status": link.verification_status,
                    "valid_from": link.valid_from.isoformat() if link.valid_from else None,
                    "valid_to": link.valid_to.isoformat() if link.valid_to else None,
                }
            )
            chain = self.supply_chain.find_supply_chain(
                db, company.id, as_of=as_of, max_hops=max_tier
            )
            paths = sorted(chain.paths, key=lambda p: (p.tier, tuple(p.node_path)))
            node_ids = {node for path in paths for node in path.node_path}
            nodes = (
                {
                    c.id: c
                    for c in db.query(Company)
                    .options(selectinload(Company.securities))
                    .filter(Company.id.in_(node_ids))
                    .all()
                }
                if node_ids
                else {}
            )
            for supplier_id in sorted(chain.all_suppliers):
                supplier = nodes[supplier_id]
                securities = sorted(
                    (
                        s
                        for s in supplier.securities
                        if s.market in {"KOSPI", "KOSDAQ"}
                        and s.ticker
                        and s.listed_at is not None
                        and s.listed_at <= day
                        and (s.delisted_at is None or day < s.delisted_at)
                        and _within(day, s.effective_from, s.effective_to)
                    ),
                    key=lambda s: s.id,
                )
                if supplier.country_code != "KR" or not securities:
                    result["excluded_companies"].append(
                        {
                            "company_id": supplier_id,
                            "role": "candidate",
                            "anchor_id": company.id,
                            "reason": "not_korean_company"
                            if supplier.country_code != "KR"
                            else "no_valid_kospi_kosdaq_security_as_of",
                        }
                    )
                    continue
                candidate = candidates.setdefault(
                    supplier_id,
                    {
                        **_company(supplier),
                        "classification": "supply_chain_candidate",
                        "securities": [
                            {
                                "security_id": s.id,
                                "ticker": s.ticker,
                                "market": s.market,
                                "listed_at": s.listed_at.isoformat(),
                                "delisted_at": s.delisted_at.isoformat() if s.delisted_at else None,
                                "effective_from": s.effective_from.isoformat()
                                if s.effective_from
                                else None,
                                "effective_to": s.effective_to.isoformat()
                                if s.effective_to
                                else None,
                            }
                            for s in securities
                        ],
                        "paths": [],
                    },
                )
                for path in (p for p in paths if p.supplier_id == supplier_id):
                    candidate["paths"].append(
                        {
                            "anchor_id": company.id,
                            "tier": path.tier,
                            "node_path": path.node_path,
                            "nodes": [_company(nodes[n]) for n in path.node_path],
                            "relationships": [
                                {
                                    "relationship_id": edge.relationship_id,
                                    "source_relationship_ids": sorted(edge.source_relationship_ids),
                                    "source_relationships": [
                                        {
                                            "relationship_id": original.id,
                                            "original_type": original.type,
                                            "source_id": original.source_id,
                                            "target_id": original.target_id,
                                            "verification_status": original.status,
                                            "valid_from": original.valid_from.isoformat()
                                            if original.valid_from
                                            else None,
                                            "valid_to": original.valid_to.isoformat()
                                            if original.valid_to
                                            else None,
                                            "confidence": original.confidence,
                                        }
                                        for rid in sorted(edge.source_relationship_ids)
                                        if (original := db.get(Relationship, rid)) is not None
                                    ],
                                    "supplier_id": edge.supplier_id,
                                    "customer_id": edge.customer_id,
                                    "original_type": edge.original_type,
                                    "verification_status": edge.status,
                                    "confidence": edge.confidence,
                                    "valid_from": edge.valid_from.isoformat()
                                    if edge.valid_from
                                    else None,
                                    "valid_to": edge.valid_to.isoformat()
                                    if edge.valid_to
                                    else None,
                                    "evidence": [
                                        _evidence(ev)
                                        for ev in sorted(edge.evidences, key=lambda ev: ev.id)
                                    ],
                                }
                                for edge in path.edge_path
                            ],
                        }
                    )
            # Explain rejected edges adjacent to the reachable exploration frontier only.
            frontier = {company.id} | {
                sid for sid, tier in chain.all_suppliers.items() if tier < max_tier
            }
            relationships = (
                db.query(Relationship)
                .options(selectinload(Relationship.evidences))
                .filter(
                    Relationship.type.in_(VALID_SUPPLY_TYPES),
                    ((Relationship.type == "SUPPLIES_TO") & Relationship.target_id.in_(frontier))
                    | ((Relationship.type == "CUSTOMER_OF") & Relationship.source_id.in_(frontier)),
                )
                .order_by(Relationship.id)
                .all()
            )
            for rel in relationships:
                if self.supply_chain.normalize_relationship(rel, as_of) is None:
                    result["excluded_relationships"].append(
                        {
                            "anchor_id": company.id,
                            "relationship_id": rel.id,
                            "source_id": rel.source_id,
                            "target_id": rel.target_id,
                            "reason": "relationship_not_verified"
                            if rel.status != "verified"
                            else "relationship_inactive_or_no_verified_evidence_available_as_of",
                        }
                    )
        for supplier_id in sorted(candidates):
            candidate = candidates[supplier_id]
            candidate["paths"].sort(
                key=lambda p: (p["tier"], p["anchor_id"], tuple(p["node_path"]))
            )
            candidate["representative_path"] = candidate["paths"][0]
            candidate["representative_tier"] = candidate["paths"][0]["tier"]
            candidate["alternative_paths"] = candidate["paths"][1:]
            result["candidates"].append(candidate)
        if not result["anchors"]:
            return empty("no_verified_us_anchor_with_valid_evidence_and_identifiers")
        if not result["candidates"]:
            return empty("no_korean_listed_supply_chain_candidate_as_of")
        return result
