from typing import Optional

from sqlalchemy.orm import Session

from app.adapters.dart_adapter import DartAdapter
from app.models.schema import Company

INDUSTRY_PREFIXES = {
    "01": "AGRICULTURE",
    "02": "FORESTRY",
    "03": "FISHING",
    "05": "MINING",
    "06": "MINING",
    "07": "MINING",
    "08": "MINING",
    "10": "FOOD_BEVERAGE",
    "11": "FOOD_BEVERAGE",
    "12": "TOBACCO",
    "13": "TEXTILES_APPAREL",
    "14": "TEXTILES_APPAREL",
    "15": "TEXTILES_APPAREL",
    "19": "ENERGY_CHEMICALS",
    "20": "ENERGY_CHEMICALS",
    "21": "PHARMA_BIOTECH",
    "22": "MATERIALS",
    "23": "MATERIALS",
    "24": "METALS",
    "25": "INDUSTRIALS",
    "26": "SEMICONDUCTORS_ELECTRONICS",
    "27": "ELECTRICAL_EQUIPMENT",
    "28": "ELECTRICAL_EQUIPMENT",
    "29": "MACHINERY",
    "30": "AUTOMOBILES",
    "31": "TRANSPORT_EQUIPMENT",
    "32": "CONSUMER_GOODS",
    "33": "INDUSTRIALS",
    "34": "INDUSTRIALS",
    "35": "UTILITIES",
    "36": "UTILITIES",
    "37": "ENVIRONMENT",
    "38": "ENVIRONMENT",
    "39": "ENVIRONMENT",
    "41": "CONSTRUCTION",
    "42": "CONSTRUCTION",
    "45": "AUTOMOBILES",
    "46": "WHOLESALE_RETAIL",
    "47": "WHOLESALE_RETAIL",
    "49": "TRANSPORT_LOGISTICS",
    "50": "TRANSPORT_LOGISTICS",
    "51": "TRANSPORT_LOGISTICS",
    "52": "TRANSPORT_LOGISTICS",
    "55": "HOSPITALITY",
    "56": "HOSPITALITY",
    "58": "MEDIA_CONTENT",
    "59": "MEDIA_CONTENT",
    "60": "MEDIA_CONTENT",
    "61": "TELECOMMUNICATIONS",
    "62": "SOFTWARE_IT",
    "63": "SOFTWARE_IT",
    "64": "FINANCIALS",
    "65": "FINANCIALS",
    "66": "FINANCIALS",
    "68": "REAL_ESTATE",
    "70": "PROFESSIONAL_SERVICES",
    "71": "PROFESSIONAL_SERVICES",
    "72": "PROFESSIONAL_SERVICES",
    "73": "PROFESSIONAL_SERVICES",
    "74": "BUSINESS_SERVICES",
    "75": "BUSINESS_SERVICES",
    "85": "EDUCATION",
    "86": "HEALTHCARE",
    "90": "LEISURE",
    "91": "LEISURE",
    "95": "CONSUMER_SERVICES",
    "96": "CONSUMER_SERVICES",
}


def normalize_industry_code(value: object) -> Optional[str]:
    digits = "".join(character for character in str(value or "") if character.isdigit())
    return digits or None


def classify_industry(code: object) -> str:
    normalized = normalize_industry_code(code)
    if not normalized:
        return "UNCLASSIFIED"
    return INDUSTRY_PREFIXES.get(normalized[:2], "OTHER")


def sync_company_industry(db: Session, adapter: DartAdapter, company: Company) -> dict[str, Optional[str]]:
    if not company.corp_code:
        raise ValueError("company has no DART corp_code")
    profile = adapter.company_profile(company.corp_code)
    code = normalize_industry_code(profile.get("induty_code"))
    industry_id = classify_industry(code)
    company.industry_id = industry_id
    db.commit()
    return {"industry_code": code, "industry_id": industry_id}