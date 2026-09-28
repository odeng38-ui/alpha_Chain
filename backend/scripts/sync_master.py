import argparse
import json
from datetime import date

from app.config import settings
from app.db.session import SessionLocal
from app.services.master_batch import sync_provider_master


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--supplement-csv")
    args = parser.parse_args()
    with SessionLocal() as db:
        result = sync_provider_master(
            db, settings.DART_API_KEY, args.as_of, args.supplement_csv,
            settings.KRX_ID, settings.KRX_PW,
        )
    print(json.dumps(result, ensure_ascii=False, default=str, indent=2))


if __name__ == "__main__":
    main()
