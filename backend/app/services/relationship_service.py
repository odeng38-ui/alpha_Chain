import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Set, Tuple

from sqlalchemy.orm import Session

from app.models.schema import (
    Company,
    Filing,
    IdentifierMap,
    Relationship,
    RelationshipEvidence,
)

RELATIONSHIP_TYPES = {
    "SUPPLIES_TO", "CUSTOMER_OF", "PARTNERS_WITH",
    "COMPETES_WITH", "OWNS", "BELONGS_TO",
}
RULES = (
    ("SUPPLIES_TO", re.compile(r"공급|납품|수주|공급계약"), 0.88),
    ("CUSTOMER_OF", re.compile(r"고객사|주요고객|매출처"), 0.78),
    ("PARTNERS_WITH", re.compile(r"협력|파트너|공동개발|업무협약"), 0.76),
    ("COMPETES_WITH", re.compile(r"경쟁사|경쟁업체|시장경쟁"), 0.73),
    ("OWNS", re.compile(r"자회사|종속회사|지분을 보유|인수"), 0.80),
    ("BELONGS_TO", re.compile(r"계열사|소속|종속되어"), 0.77),
)


def normalize_company_name(value: str) -> str:
    value = re.sub(r"\(주\)|㈜|주식회사|유한회사| Co\.,? Ltd\.?", "", value, flags=re.I)
    return re.sub(r"[^0-9A-Za-z가-힣]", "", value).casefold()


@dataclass(frozen=True)
class RelationshipCandidate:
    target_name: str
    relationship_type: str
    evidence_text: str
    evidence_location: Optional[str] = None
    confidence: float = 0.0


class RelationshipExtractionService:
    def _company_names(self, db: Session) -> Dict[str, Set[int]]:
        names: Dict[str, Set[int]] = {}
        for company_id, name in db.query(Company.id, Company.name):
            names.setdefault(normalize_company_name(name), set()).add(company_id)
        aliases = db.query(IdentifierMap).filter(IdentifierMap.source == "COMPANY_ALIAS").all()
        for alias in aliases:
            if alias.company_id:
                names.setdefault(normalize_company_name(alias.source_id_value), set()).add(alias.company_id)
        return names

    def rule_candidates(self, db: Session, source_id: int, text: str) -> List[RelationshipCandidate]:
        names = self._company_names(db)
        result = []
        sentences = [part.strip() for part in re.split(r"(?<=[.!?。])\s+|[\r\n]+", text) if part.strip()]
        for sentence_no, sentence in enumerate(sentences, 1):
            normalized_sentence = normalize_company_name(sentence)
            matches = []
            for normalized_name, company_ids in names.items():
                if normalized_name and normalized_name in normalized_sentence and company_ids != {source_id}:
                    matches.append((normalized_name, company_ids))
            for relationship_type, pattern, confidence in RULES:
                if not pattern.search(sentence):
                    continue
                for normalized_name, company_ids in matches:
                    if len(company_ids) != 1:
                        continue
                    target_id = next(iter(company_ids))
                    if target_id == source_id:
                        continue
                    target = db.get(Company, target_id)
                    result.append(RelationshipCandidate(
                        target_name=target.name, relationship_type=relationship_type,
                        evidence_text=sentence, evidence_location=f"sentence:{sentence_no}",
                        confidence=confidence,
                    ))
        return result

    def persist_candidates(
        self, db: Session, source_id: int, candidates: Iterable[RelationshipCandidate],
        source_document: str, source_url: Optional[str] = None,
        filing_id: Optional[str] = None, published_at: Optional[datetime] = None,
        extractor_version: str = "rule_v1", auto_verify_threshold: Optional[float] = None,
    ) -> Dict[str, int]:
        if not db.get(Company, source_id):
            raise ValueError("source company not found")
        names = self._company_names(db)
        edges = evidences = skipped = 0
        for candidate in candidates:
            if candidate.relationship_type not in RELATIONSHIP_TYPES or not candidate.evidence_text.strip():
                skipped += 1
                continue
            target_ids = names.get(normalize_company_name(candidate.target_name), set())
            if len(target_ids) != 1:
                skipped += 1
                continue
            target_id = next(iter(target_ids))
            if target_id == source_id:
                skipped += 1
                continue
            edge = db.query(Relationship).filter_by(
                source_id=source_id, target_id=target_id, type=candidate.relationship_type,
            ).first()
            if edge is None:
                status = "proposed"
                if auto_verify_threshold is not None and candidate.confidence >= auto_verify_threshold:
                    status = "verified"
                edge = Relationship(
                    source_id=source_id, target_id=target_id, type=candidate.relationship_type,
                    status=status, confidence=candidate.confidence,
                    extractor_version=extractor_version,
                )
                db.add(edge)
                db.flush()
                edges += 1
            evidence_hash = hashlib.sha256("|".join((
                str(edge.id), source_document, candidate.evidence_location or "",
                candidate.evidence_text.strip(),
            )).encode("utf-8")).hexdigest()
            exists = db.query(RelationshipEvidence).filter_by(
                relationship_id=edge.id, evidence_hash=evidence_hash,
            ).first()
            if exists:
                continue
            filing = db.get(Filing, filing_id) if filing_id else None
            db.add(RelationshipEvidence(
                relationship_id=edge.id, filing_id=filing_id,
                excerpt=candidate.evidence_text.strip(), location=candidate.evidence_location,
                published_at=published_at or (filing.available_at if filing else datetime.utcnow()),
                extractor_version=extractor_version, source_document=source_document,
                source_url=source_url, confidence=candidate.confidence,
                evidence_hash=evidence_hash,
            ))
            evidences += 1
        db.commit()
        return {"relationships": edges, "evidences": evidences, "skipped": skipped}

    def extract_and_persist(self, db: Session, source_id: int, text: str, **kwargs) -> Dict[str, int]:
        return self.persist_candidates(db, source_id, self.rule_candidates(db, source_id, text), **kwargs)

    @staticmethod
    def evaluate(predicted: Iterable[Tuple[int, int, str]], gold: Iterable[Tuple[int, int, str]]) -> Dict[str, float]:
        predicted_set, gold_set = set(predicted), set(gold)
        true_positive = len(predicted_set & gold_set)
        precision = true_positive / len(predicted_set) if predicted_set else 0.0
        recall = true_positive / len(gold_set) if gold_set else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        return {
            "true_positive": true_positive, "predicted": len(predicted_set), "gold": len(gold_set),
            "precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4),
        }
