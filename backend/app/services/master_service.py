import re
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.schema import Company, IdentifierMap, Security


class CompanySecurityMasterService:
    @staticmethod
    def classify_security_type(name: str, ticker: str) -> str:
        """
        보통주(COMMON), 우선주(PREFERRED), ETF, ETN, SPAC 구분 로직
        """
        name_upper = name.upper()
        ticker_str = str(ticker).strip()

        # 1. SPAC
        if "스팩" in name or "SPAC" in name_upper:
            return "SPAC"

        # 2. ETN
        if " ETN" in name_upper or name_upper.endswith("ETN"):
            return "ETN"

        # 3. ETF
        etf_brands = ["KODEX", "TIGER", "ACE", "RISE", "SOL", "PLUS", "HANARO", "KBSTAR", "ARIRANG", "KOSEF", "FOCUS", "TIMEFOLIO", "WOORI", "UNIFEX"]
        if "ETF" in name_upper or any(brand in name_upper for brand in etf_brands):
            return "ETF"

        # 4. 우선주 (PREFERRED): 종목명에 '우', '우B', '우C', '우선주' 등이 포함되어 있는 경우
        if re.search(r"(우[ABC0-9]?|우B|우C|우선주)$", name) or ("우선주" in name) or ("우B" in name) or ("우C" in name) or (name.endswith("우") and len(ticker_str) == 6 and ticker_str[-1] in ['5', '7', '9', 'K', 'L', 'M']):
            return "PREFERRED"

        return "COMMON"


    @classmethod
    def upsert_master_record(cls, db: Session, record: Dict[str, Any]) -> Tuple[Company, Security, bool]:
        """
        단일 기업/종목 마스터 레코드 멱등 수집/업데이트
        record schema:
          - corp_code (str, 8자리 DART 고유번호)
          - name (str, 기업명)
          - ticker (str, 종목코드)
          - market (str, KOSPI / KOSDAQ)
          - isin (optional str)
          - security_type (optional str)
          - industry_id (optional str)
          - status (optional str, default "ACTIVE")
          - listed_at (optional date)
          - delisted_at (optional date)
        """
        corp_code = str(record["corp_code"]).zfill(8)
        name = record["name"].strip()
        ticker = str(record["ticker"]).zfill(6)
        market = record.get("market", "KOSPI").upper()
        isin = record.get("isin")
        status = record.get("status", "ACTIVE").upper()
        industry_id = record.get("industry_id")
        listed_at = record.get("listed_at")
        delisted_at = record.get("delisted_at")

        security_type = record.get("security_type")
        if not security_type:
            security_type = cls.classify_security_type(name, ticker)

        is_new = False

        # 1. Company 조회 및 생성을 멱등하게 수행
        company = db.query(Company).filter(Company.corp_code == corp_code).first()
        if not company:
            # 이름으로 기존 회사 연관 여부 재확인
            company = db.query(Company).filter(Company.name == name).first()

        if not company:
            company = Company(
                corp_code=corp_code,
                name=name,
                status=status,
                industry_id=industry_id
            )
            db.add(company)
            db.flush()
            is_new = True
        else:
            # 기존 회사 정보 업데이트
            if name and company.name != name:
                company.name = name
            if status and company.status != status:
                company.status = status
            if industry_id and company.industry_id != industry_id:
                company.industry_id = industry_id

        # 2. Security 조회 및 생성/업데이트
        observed_at = date.today()
        transition_at = listed_at or observed_at
        security = db.query(Security).filter(
            Security.ticker == ticker,
            Security.effective_to.is_(None),
        ).first()
        if security and security.company_id != company.id:
            security.effective_to = transition_at
            if security.delisted_at is None:
                security.delisted_at = transition_at
            for identifier in security.identifier_maps:
                if identifier.effective_to is None:
                    identifier.effective_to = transition_at
            security = None
        if not security:
            effective_from = listed_at
            effective_to = delisted_at if status == "DELISTED" else None
            security = Security(
                company_id=company.id,
                market=market,
                ticker=ticker,
                isin=isin,
                security_type=security_type,
                listed_at=listed_at,
                delisted_at=delisted_at,
                effective_from=effective_from,
                effective_to=effective_to
            )
            db.add(security)
            db.flush()
        else:
            if isin and security.isin != isin:
                security.isin = isin
            if market and security.market != market:
                security.market = market
            if security_type and security.security_type != security_type:
                security.security_type = security_type
            if delisted_at:
                security.delisted_at = delisted_at
                security.effective_to = delisted_at

        # 3. IdentifierMap 동기화
        cls._upsert_identifier_map(db, company_id=company.id, security_id=security.id, source="DART_CORP_CODE", source_val=corp_code, effective_from=observed_at)
        cls._upsert_identifier_map(db, company_id=company.id, security_id=security.id, source="KRX_TICKER", source_val=ticker, effective_from=observed_at)
        if isin:
            cls._upsert_identifier_map(db, company_id=company.id, security_id=security.id, source="ISIN", source_val=isin, effective_from=observed_at)
        if record.get("eng_ticker"):
            cls._upsert_identifier_map(db, company_id=company.id, security_id=security.id, source="ENG_TICKER", source_val=record["eng_ticker"], effective_from=observed_at)

        return company, security, is_new

    @classmethod
    def _upsert_identifier_map(cls, db: Session, company_id: int, security_id: Optional[int], source: str, source_val: str, effective_from: Optional[date] = None):
        id_map = db.query(IdentifierMap).filter(
            IdentifierMap.source == source,
            IdentifierMap.source_id_value == source_val,
            IdentifierMap.effective_to.is_(None),
        ).first()

        if not id_map:
            id_map = IdentifierMap(
                company_id=company_id,
                security_id=security_id,
                source=source,
                source_id_value=source_val,
                is_primary=True,
                effective_from=effective_from or date.today(),
            )
            db.add(id_map)
        else:
            if id_map.company_id != company_id or id_map.security_id != security_id:
                id_map.company_id = company_id
                id_map.security_id = security_id

    @classmethod
    def sync_master_records(cls, db: Session, records: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        배치 수집 및 적재 실행
        """
        inserted_cnt = 0
        updated_cnt = 0
        errors = []

        for idx, rec in enumerate(records):
            try:
                _, _, is_new = cls.upsert_master_record(db, rec)
                if is_new:
                    inserted_cnt += 1
                else:
                    updated_cnt += 1
            except Exception as e:
                errors.append({"index": idx, "record": rec, "error": str(e)})

        db.commit()
        return {
            "total_processed": len(records),
            "inserted": inserted_cnt,
            "updated": updated_cnt,
            "errors_count": len(errors),
            "errors": errors
        }
    @classmethod
    def sync_master_records_bulk(cls, db: Session, records: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Bulk upsert trusted provider records with a bounded number of DB round trips."""
        if not records:
            return {
                "total_processed": 0,
                "inserted": 0,
                "updated": 0,
                "errors_count": 0,
                "errors": [],
            }

        corp_codes = {str(record["corp_code"]).zfill(8) for record in records}
        tickers = {str(record["ticker"]).zfill(6) for record in records}
        companies = {
            company.corp_code: company
            for company in db.query(Company).filter(Company.corp_code.in_(corp_codes)).all()
        }
        inserted_codes = set()

        for record in records:
            corp_code = str(record["corp_code"]).zfill(8)
            company = companies.get(corp_code)
            if company is None:
                company = Company(
                    corp_code=corp_code,
                    name=record["name"].strip(),
                    status=record.get("status", "ACTIVE").upper(),
                    industry_id=record.get("industry_id"),
                )
                db.add(company)
                companies[corp_code] = company
                inserted_codes.add(corp_code)
            else:
                company.name = record["name"].strip()
                company.status = record.get("status", "ACTIVE").upper()
                if record.get("industry_id"):
                    company.industry_id = record["industry_id"]
        db.flush()

        securities = {
            security.ticker: security
            for security in db.query(Security).filter(
                Security.ticker.in_(tickers), Security.effective_to.is_(None)
            ).all()
        }
        ordered_securities = []
        for record in records:
            corp_code = str(record["corp_code"]).zfill(8)
            ticker = str(record["ticker"]).zfill(6)
            company = companies[corp_code]
            security = securities.get(ticker)
            if security is None:
                security = Security(
                    company_id=company.id,
                    market=record.get("market", "UNKNOWN").upper(),
                    ticker=ticker,
                    isin=record.get("isin"),
                    security_type=record.get("security_type")
                    or cls.classify_security_type(record["name"].strip(), ticker),
                    listed_at=record.get("listed_at"),
                    delisted_at=record.get("delisted_at"),
                    effective_from=record.get("listed_at"),
                    effective_to=(
                        record.get("delisted_at")
                        if record.get("status", "ACTIVE").upper() == "DELISTED"
                        else None
                    ),
                )
                db.add(security)
                securities[ticker] = security
            else:
                security.company_id = company.id
                security.market = record.get("market", security.market).upper()
                security.security_type = record.get(
                    "security_type"
                ) or cls.classify_security_type(record["name"].strip(), ticker)
                if record.get("isin"):
                    security.isin = record["isin"]
            ordered_securities.append(security)
        db.flush()

        identifier_specs = []
        observed_at = date.today()
        for record, security in zip(records, ordered_securities):
            company = companies[str(record["corp_code"]).zfill(8)]
            identifier_specs.extend(
                [
                    (
                        "DART_CORP_CODE",
                        str(record["corp_code"]).zfill(8),
                        company.id,
                        security.id,
                    ),
                    (
                        "KRX_TICKER",
                        str(record["ticker"]).zfill(6),
                        company.id,
                        security.id,
                    ),
                ]
            )
            if record.get("isin"):
                identifier_specs.append(("ISIN", record["isin"], company.id, security.id))
            if record.get("eng_ticker"):
                identifier_specs.append(
                    ("ENG_TICKER", record["eng_ticker"], company.id, security.id)
                )

        sources = {spec[0] for spec in identifier_specs}
        values = {spec[1] for spec in identifier_specs}
        identifiers = {
            (identifier.source, identifier.source_id_value): identifier
            for identifier in db.query(IdentifierMap).filter(
                IdentifierMap.source.in_(sources),
                IdentifierMap.source_id_value.in_(values),
                IdentifierMap.effective_to.is_(None),
            ).all()
        }
        for source, value, company_id, security_id in identifier_specs:
            identifier = identifiers.get((source, value))
            if identifier is None:
                identifier = IdentifierMap(
                    company_id=company_id,
                    security_id=security_id,
                    source=source,
                    source_id_value=value,
                    is_primary=True,
                    effective_from=observed_at,
                )
                db.add(identifier)
                identifiers[(source, value)] = identifier
            else:
                identifier.company_id = company_id
                identifier.security_id = security_id

        db.commit()
        inserted = len(inserted_codes)
        return {
            "total_processed": len(records),
            "inserted": inserted,
            "updated": len(records) - inserted,
            "errors_count": 0,
            "errors": [],
        }
