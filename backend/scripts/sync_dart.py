import argparse
import json
from datetime import date, timedelta

from app.adapters.dart_adapter import DartAdapter
from app.config import settings
from app.db.session import SessionLocal
from app.models.schema import Company
from app.services.dart_service import DartCollectionService


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--download-documents", action="store_true")
    parser.add_argument("--financial-year")
    parser.add_argument("--report-code", default="11011")
    args = parser.parse_args()
    service = DartCollectionService(DartAdapter(settings.DART_API_KEY), settings.DART_RAW_DIR)
    results = []
    with SessionLocal() as db:
        companies = db.query(Company).filter(Company.corp_code.isnot(None)).limit(args.limit).all()
        for company in companies:
            result = service.sync_company(
                db, company, date.today() - timedelta(days=args.days), date.today(),
                args.download_documents,
            )
            results.append({"company_id": company.id, **result})
            if args.financial_year:
                results[-1]["financial_facts"] = service.sync_financials(
                    db, company, args.financial_year, args.report_code
                )
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
