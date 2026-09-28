from datetime import date

from app.db.session import SessionLocal
from app.services.master_service import CompanySecurityMasterService


def main() -> None:
    with SessionLocal() as db:
        result = CompanySecurityMasterService.sync_master_records(db, [{
            "corp_code": "00126380",
            "name": "삼성전자",
            "ticker": "005930",
            "market": "KOSPI",
            "security_type": "COMMON",
            "listed_at": date(1975, 6, 11),
        }])
    print(result)


if __name__ == "__main__":
    main()
