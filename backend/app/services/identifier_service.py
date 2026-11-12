import re
import unicodedata
from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from app.models.schema import Company, IdentifierMap

IDENTIFIER_SOURCES = {
    "DART_CORP_CODE", "CIK", "LEI", "US_TICKER", "KRX_TICKER", "ISIN",
    "LEGAL_NAME_ALIAS", "COMPANY_ALIAS", "ENG_TICKER",
}
EXCLUSIVE_IDENTIFIER_SOURCES = {
    "DART_CORP_CODE", "CIK", "LEI", "US_TICKER", "KRX_TICKER", "ISIN",
}


class InvalidIdentifierError(ValueError):
    pass


class IdentifierConflictError(ValueError):
    def __init__(self, source: str, value: str):
        super().__init__(f"active {source} identifier is already assigned to another company")
        self.source = source
        self.value = value


def normalize_company_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    return " ".join(normalized.split()).casefold()


def normalize_identifier(source: str, value: str) -> str:
    normalized_source = source.strip().upper()
    if normalized_source not in IDENTIFIER_SOURCES:
        raise InvalidIdentifierError("unsupported identifier source")
    normalized_value = unicodedata.normalize("NFKC", str(value)).strip()
    if not normalized_value:
        raise InvalidIdentifierError("identifier value is required")
    if normalized_source == "CIK":
        if not re.fullmatch(r"\d{1,10}", normalized_value):
            raise InvalidIdentifierError("CIK must contain 1 to 10 digits")
        return normalized_value.zfill(10)
    if normalized_source == "DART_CORP_CODE":
        if not re.fullmatch(r"\d{1,8}", normalized_value):
            raise InvalidIdentifierError("DART corp code must contain 1 to 8 digits")
        return normalized_value.zfill(8)
    if normalized_source == "LEI":
        normalized_value = normalized_value.upper()
        if not re.fullmatch(r"[A-Z0-9]{20}", normalized_value):
            raise InvalidIdentifierError("LEI must contain 20 alphanumeric characters")
        return normalized_value
    if normalized_source == "ISIN":
        normalized_value = normalized_value.upper()
        if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}\d", normalized_value):
            raise InvalidIdentifierError("ISIN must contain 12 valid characters")
        return normalized_value
    if normalized_source == "KRX_TICKER":
        normalized_value = normalized_value.upper()
        if not re.fullmatch(r"[A-Z0-9]{1,6}", normalized_value):
            raise InvalidIdentifierError("KRX ticker must contain 1 to 6 alphanumeric characters")
        return normalized_value.zfill(6) if normalized_value.isdigit() else normalized_value
    if normalized_source == "US_TICKER":
        normalized_value = normalized_value.upper()
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9.-]{0,19}", normalized_value):
            raise InvalidIdentifierError("US ticker has an invalid format")
        return normalized_value
    if normalized_source in {"LEGAL_NAME_ALIAS", "COMPANY_ALIAS"}:
        return normalize_company_name(normalized_value)
    return normalized_value.upper()


class IdentifierService:
    @staticmethod
    def register(
        db: Session, *, company_id: int, source: str, value: str,
        security_id: Optional[int] = None, is_primary: bool = True,
        effective_from: Optional[date] = None,
    ) -> IdentifierMap:
        if db.get(Company, company_id) is None:
            raise LookupError("company not found")
        normalized_source = source.strip().upper()
        normalized_value = normalize_identifier(normalized_source, value)
        active = db.query(IdentifierMap).filter(
            IdentifierMap.source == normalized_source,
            IdentifierMap.source_id_value == normalized_value,
            IdentifierMap.effective_to.is_(None),
        ).all()
        if normalized_source in EXCLUSIVE_IDENTIFIER_SOURCES:
            if any(row.company_id != company_id for row in active):
                raise IdentifierConflictError(normalized_source, normalized_value)
            existing = next((row for row in active if row.company_id == company_id), None)
            if existing is not None:
                existing.security_id = security_id
                existing.is_primary = is_primary
                return existing
        existing = next((
            row for row in active
            if row.company_id == company_id and row.security_id == security_id
        ), None)
        if existing is not None:
            existing.is_primary = is_primary
            return existing
        identifier = IdentifierMap(
            company_id=company_id, security_id=security_id,
            source=normalized_source, source_id_value=normalized_value,
            is_primary=is_primary, effective_from=effective_from or date.today(),
        )
        db.add(identifier)
        return identifier