import json
from datetime import date, timedelta

from sqlalchemy import func

from app.db.session import SessionLocal
from app.models.schema import DailyPrice, Security
from app.services.price_quality import PriceQualityService
from app.services.report_service import MappingReportService


def main() -> None:
    cutoff = date.today() - timedelta(days=365 * 5)
    with SessionLocal() as db:
        mapping = MappingReportService.generate_mapping_report(db)
        active = db.query(Security).filter(
            Security.security_type == "COMMON", Security.effective_to.is_(None)
        ).count()
        covered = db.query(DailyPrice.security_id).join(Security).filter(
            Security.security_type == "COMMON",
            Security.effective_to.is_(None),
            DailyPrice.trade_date <= cutoff + timedelta(days=10),
        ).distinct().count()
        duplicate_groups = db.query(
            DailyPrice.security_id, DailyPrice.trade_date
        ).group_by(
            DailyPrice.security_id, DailyPrice.trade_date
        ).having(func.count() > 1).count()
        negative_volumes = db.query(DailyPrice).filter(DailyPrice.volume < 0).count()
        quality = PriceQualityService().check_all(db, limit=10000)

    passed = (
        mapping["summary"]["target_99pct_met"]
        and active > 0
        and covered == active
        and duplicate_groups == 0
        and negative_volumes == 0
        and quality["total_missing"] == 0
    )
    result = {
        "passed": passed,
        "mapping": mapping["summary"],
        "active_common_securities": active,
        "five_year_covered_securities": covered,
        "duplicate_groups": duplicate_groups,
        "negative_volumes": negative_volumes,
        "quality": {k: v for k, v in quality.items() if k != "reports"},
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
