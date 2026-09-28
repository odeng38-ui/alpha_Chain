from typing import Any, Dict, List

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.schema import IdentifierMap, Security


class MappingReportService:
    @classmethod
    def generate_mapping_report(cls, db: Session) -> Dict[str, Any]:
        """
        DART 기업과 시세 종목의 식별자 매핑률 산출 및 리포트 생성
        완료 승인 기준: 대상 보통주 식별자 매핑률 99% 이상
        """
        # 1. 보통주 대상 전체 수 (보통주 && ACTIVE)
        common_securities = db.query(Security).filter(
            Security.security_type == "COMMON",
            Security.effective_to.is_(None),
        ).all()

        total_common_count = len(common_securities)
        mapped_common_count = 0
        unmapped_list: List[Dict[str, Any]] = []

        for sec in common_securities:
            # DART corp_code 및 KRX ticker의 IdentifierMap 이 존재하는지 검사
            id_maps = db.query(IdentifierMap).filter(
                IdentifierMap.security_id == sec.id
            ).all()

            sources = {im.source for im in id_maps}
            has_dart = "DART_CORP_CODE" in sources or (sec.company and sec.company.corp_code)
            has_ticker = "KRX_TICKER" in sources or bool(sec.ticker)

            if has_dart and has_ticker:
                mapped_common_count += 1
            else:
                unmapped_list.append({
                    "security_id": sec.id,
                    "company_id": sec.company_id,
                    "company_name": sec.company.name if sec.company else "UNKNOWN",
                    "ticker": sec.ticker,
                    "corp_code": sec.company.corp_code if sec.company else None,
                    "missing_reason": "MISSING_DART_CODE" if not has_dart else "MISSING_TICKER"
                })

        mapping_rate = (mapped_common_count / total_common_count * 100.0) if total_common_count > 0 else 0.0
        target_met = total_common_count > 0 and mapping_rate >= 99.0

        # 2. 종목코드 / Corp_code 중복 검사
        duplicate_tickers = cls._check_duplicate_tickers(db)

        return {
            "summary": {
                "total_common_securities": total_common_count,
                "mapped_common_securities": mapped_common_count,
                "unmapped_common_securities": len(unmapped_list),
                "mapping_rate_percent": round(mapping_rate, 2),
                "target_99pct_met": target_met
            },
            "unmapped_list": unmapped_list,
            "duplicate_tickers": duplicate_tickers
        }

    @classmethod
    def _check_duplicate_tickers(cls, db: Session) -> List[Dict[str, Any]]:
        """
        동일 종목코드 중복 검사 리포트
        """
        dups = db.query(
            Security.ticker, func.count(Security.id).label("count")
        ).group_by(Security.ticker).having(func.count(Security.id) > 1).all()

        results = []
        for ticker, cnt in dups:
            secs = db.query(Security).filter(Security.ticker == ticker).all()
            results.append({
                "ticker": ticker,
                "count": cnt,
                "securities": [
                    {
                        "security_id": s.id,
                        "company_name": s.company.name if s.company else "UNKNOWN",
                        "market": s.market,
                        "security_type": s.security_type
                    } for s in secs
                ]
            })
        return results
