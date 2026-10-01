from concurrent.futures import ThreadPoolExecutor, as_completed
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

def sync_industry_batch(db: Session, adapter: DartAdapter, after_id: int = 0, batch_size: int = 25):
    companies = db.query(Company).filter(
        Company.id > after_id,
        Company.corp_code.isnot(None),
        Company.industry_id.is_(None),
    ).order_by(Company.id).limit(batch_size).all()
    profiles = {}
    failures = []

    def fetch(company_id: int, corp_code: str):
        return company_id, adapter.company_profile(corp_code)

    workers = min(8, len(companies)) or 1
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(fetch, company.id, company.corp_code): company.id
            for company in companies
        }
        for future in as_completed(futures):
            company_id = futures[future]
            try:
                _, profile = future.result()
                profiles[company_id] = profile
            except Exception as exc:
                failures.append({"company_id": company_id, "error": str(exc)})

    for company in companies:
        profile = profiles.get(company.id)
        if profile is not None:
            company.industry_id = classify_industry(profile.get("induty_code"))
    db.commit()
    next_after_id = companies[-1].id if companies else None
    remaining = db.query(Company).filter(
        Company.corp_code.isnot(None), Company.industry_id.is_(None)
    ).count()
    failures.sort(key=lambda item: item["company_id"])
    return {"processed": len(companies), "updated": len(profiles), "failures": failures,
            "next_after_id": next_after_id, "remaining": remaining,
            "has_more": bool(companies) and remaining > 0}
