import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.schema import NewsArticle, NewsClassification


@dataclass(frozen=True)
class ClassificationResult:
    event_kind: str
    industries: list[str]
    direction: str
    confidence: float
    matched_keywords: list[str]
    rationale: str


EVENT_RULES = (
    ("FED_POLICY", ("federal reserve", "fed ", "fed's", "fed chief", "fed outlook", "interest rate", "rate hike", "rate cut", "bond yield")),
    ("INFLATION", ("inflation", "cpi", "consumer price", "pce")),
    ("TRADE_POLICY", ("tariff", "trade war", "export control", "sanction")),
    ("SEMICONDUCTOR", ("semiconductor", "chip", "chips", "nvidia", "phlx semiconductor")),
    ("AI_TECH", ("artificial intelligence", " ai ", "technology", "tech ")),
    ("ENERGY", ("crude oil", "oil ", "opec", "natural gas")),
    ("CORPORATE_EARNINGS", ("earnings", "profit", "profits", "revenue", "outlook")),
    ("MARKET_MOVEMENT", ("stock", "stocks", "market", "markets", "s&p", "nasdaq", "dow", "wall street", "bond", "bonds")),
)

INDUSTRY_RULES = {
    "FED_POLICY": ("FINANCIALS", "CONSUMER_GOODS", "INDUSTRIALS"),
    "INFLATION": ("FINANCIALS", "CONSUMER_GOODS"),
    "TRADE_POLICY": ("INDUSTRIALS", "AUTOMOBILES", "SEMICONDUCTORS_ELECTRONICS"),
    "SEMICONDUCTOR": ("SEMICONDUCTORS_ELECTRONICS", "ELECTRICAL_EQUIPMENT"),
    "AI_TECH": ("SOFTWARE_IT", "SEMICONDUCTORS_ELECTRONICS"),
    "ENERGY": ("ENERGY_CHEMICALS", "INDUSTRIALS"),
    "CORPORATE_EARNINGS": ("CONSUMER_GOODS", "INDUSTRIALS"),
    "MARKET_MOVEMENT": ("FINANCIALS", "INDUSTRIALS"),
}

POSITIVE_TERMS = (
    "rally", "rises", "rise", "gains", "gain", "higher stocks", "rate cut",
    "cooling inflation", "moderate inflation", "dovish", "beats expectations", "strong earnings",
)
NEGATIVE_TERMS = (
    "selloff", "falls", "fall", "drops", "drop", "dips", "lower", "declines", "rate hike",
    "higher interest rate", "yield climbs", "hot inflation", "misses expectations",
    "sanction", "tariff", "war", "battered", "anxiety", "hawkish", "fears", "red flag", "shed",
)


class NewsClassificationService:
    version = "news-rules-v1"

    @staticmethod
    def _find_terms(text: str, terms) -> list[str]:
        return [
            term.strip() for term in terms
            if re.search(rf"(?<![a-z0-9]){re.escape(term.strip())}(?![a-z0-9])", text)
        ]

    @staticmethod
    def classify_text(title: str) -> ClassificationResult:
        normalized = f" {title.lower()} "
        matched_by_kind = {}
        for event_kind, terms in EVENT_RULES:
            matches = NewsClassificationService._find_terms(normalized, terms)
            if matches:
                matched_by_kind[event_kind] = matches
        event_kind = next(iter(matched_by_kind), "OTHER")
        industries = []
        for kind in matched_by_kind:
            for industry in INDUSTRY_RULES[kind]:
                if industry not in industries:
                    industries.append(industry)
        direction_text = re.sub(r"\bcapital gains?\b", "", normalized)
        positive = NewsClassificationService._find_terms(direction_text, POSITIVE_TERMS)
        negative = NewsClassificationService._find_terms(direction_text, NEGATIVE_TERMS)
        if positive and negative:
            direction = "MIXED"
        elif positive:
            direction = "POSITIVE"
        elif negative:
            direction = "NEGATIVE"
        else:
            direction = "NEUTRAL"
        matched = []
        for values in matched_by_kind.values():
            matched.extend(values)
        matched.extend(term for term in positive + negative if term not in matched)
        confidence = min(0.95, 0.55 + 0.05 * len(set(matched))) if matched else 0.30
        if direction == "NEUTRAL":
            confidence = max(0.30, confidence - 0.05)
        rationale = (
            f"{event_kind} detected from {', '.join(sorted(set(matched))) or 'no rule match'}; "
            f"direction={direction}"
        )
        return ClassificationResult(
            event_kind=event_kind, industries=industries, direction=direction,
            confidence=round(confidence, 4),
            matched_keywords=sorted(set(matched)), rationale=rationale,
        )

    def classify_pending(self, db: Session, limit: int = 100, reclassify: bool = False):
        query = db.query(NewsArticle).order_by(NewsArticle.published_at.desc())
        if not reclassify:
            query = query.filter(~NewsArticle.id.in_(
                db.query(NewsClassification.news_article_id).filter(
                    NewsClassification.version == self.version
                )
            ))
        articles = query.limit(limit).all()
        created = updated = review_required = 0
        for article in articles:
            result = self.classify_text(article.title)
            row = db.query(NewsClassification).filter_by(
                news_article_id=article.id, version=self.version,
            ).first()
            if row is None:
                row = NewsClassification(news_article_id=article.id, version=self.version)
                db.add(row)
                created += 1
            else:
                updated += 1
            row.event_kind = result.event_kind
            row.industries = result.industries
            row.direction = result.direction
            row.confidence = result.confidence
            row.matched_keywords = result.matched_keywords
            row.rationale = result.rationale
            row.review_required = result.confidence < 0.60 or result.event_kind == "OTHER"
            row.classified_at = datetime.utcnow()
            review_required += int(row.review_required)
        db.commit()
        return {
            "version": self.version, "processed": len(articles),
            "created": created, "updated": updated,
            "review_required": review_required,
        }