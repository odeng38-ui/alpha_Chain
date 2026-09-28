import json
from datetime import date

from app.adapters.fred_adapter import FredAdapter
from app.config import settings
from app.db.session import SessionLocal
from app.services.fred_service import FredCollectionService


def main() -> None:
    with SessionLocal() as db:
        result = FredCollectionService(FredAdapter(settings.FRED_API_KEY)).sync(
            db, observation_start=date(2000, 1, 1)
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
