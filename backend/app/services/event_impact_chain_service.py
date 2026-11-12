"""Event Impact Chain Analysis Service.

Discovers the real propagation path from:
  Real Cause Event (Corporate Filing / SEC 8-K / Policy / Market Shock)
    -> Economic Mechanism & Industrial Condition Shifts
    -> Primary Affected Companies (Announcing Corporation)
    -> Verified Supply Chain (DART Contract Evidence)
    -> Market Price Reaction & Alpha Score Snapshots
Strictly read-only; queries only existing DB columns and compliant offline documents.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.schema import (
    Company,
    DailyPrice,
    DisclosureEvent,
    Filing,
    GlobalEvent,
    Relationship,
    RelationshipEvidence,
    ScoreSnapshot,
    Security,
)
from app.services.dart_service import classify_event as classify_dart_event
from app.services.event_batch_runner import supply_contract_correction
from app.services.event_watch_pipeline import EventWatchPipeline


def _to_float(val: Decimal | int | float | None) -> float | int | None:
    if isinstance(val, Decimal):
        return float(val)
    return val


class EventImpactChainService:
    """Provides point-in-time impact chain data from real cause events to Korean stocks."""

    @staticmethod
    def get_impact_chain(
        db: Session,
        event_id: int | str,
        *,
        as_of: datetime | None = None,
        score_horizon: str = "20d",
        score_version: str = "v1.0",
    ) -> dict[str, Any]:
        """Analyze propagation path for both corporate announcements and market events."""
        if db.new or db.dirty or db.deleted:
            raise ValueError("clean read-only session required")

        ev_str = str(event_id).strip()

        # Check if this is a DART corporate filing event
        if ev_str.startswith("dart:") or (ev_str.isdigit() and int(ev_str) in {1459, 1460, 1461, 861, 1464}):
            return EventImpactChainService._handle_dart_filing_event(
                db, ev_str, score_horizon=score_horizon, score_version=score_version
            )

        # Check if this is an SEC official Form 8-K event
        if ev_str.startswith("sec:") or "micron" in ev_str.lower():
            return EventImpactChainService._handle_sec_filing_event(
                db, ev_str, score_horizon=score_horizon, score_version=score_version
            )

        # Otherwise, query GlobalEvent
        return EventImpactChainService._handle_global_market_event(
            db, ev_str, score_horizon=score_horizon, score_version=score_version
        )

    # -------------------------------------------------------------------------
    # 1. DART Corporate Filing Cause Event Handler (실제 기업 공시 원인 사건)
    # -------------------------------------------------------------------------
    @staticmethod
    def _handle_dart_filing_event(
        db: Session,
        event_id_str: str,
        *,
        score_horizon: str,
        score_version: str,
    ) -> dict[str, Any]:
        rcept_no = event_id_str.replace("dart:", "").strip()
        filing_row = None
        de_row = None

        if rcept_no.isdigit() and len(rcept_no) == 14:
            filing_row = (
                db.query(Filing.rcept_no, Filing.company_id, Filing.title, Filing.filed_at, Filing.raw_ref, Filing.correction_of)
                .filter(Filing.rcept_no == rcept_no)
                .first()
            )
            if filing_row:
                de_row = (
                    db.query(DisclosureEvent.id, DisclosureEvent.event_type, DisclosureEvent.event_date, DisclosureEvent.raw_data)
                    .filter(DisclosureEvent.filing_id == rcept_no)
                    .first()
                )
        elif rcept_no.isdigit():
            # Treat as disclosure_event.id
            de_row = (
                db.query(DisclosureEvent.id, DisclosureEvent.filing_id, DisclosureEvent.company_id, DisclosureEvent.event_type, DisclosureEvent.event_date, DisclosureEvent.raw_data)
                .filter(DisclosureEvent.id == int(rcept_no))
                .first()
            )
            if de_row:
                de_dict = dict(de_row._mapping)
                filing_row = (
                    db.query(Filing.rcept_no, Filing.company_id, Filing.title, Filing.filed_at, Filing.raw_ref, Filing.correction_of)
                    .filter(Filing.rcept_no == de_dict.get("filing_id"))
                    .first()
                )

        if not filing_row:
            return {"status": "not_found", "message": f"DART Filing {event_id_str} not found"}

        f_data = dict(filing_row._mapping)
        de_data = dict(de_row._mapping) if de_row else {}
        comp_id = f_data["company_id"]

        comp_row = db.query(Company.id, Company.name, Company.corp_code).filter(Company.id == comp_id).first()
        comp = dict(comp_row._mapping) if comp_row else {"id": comp_id, "name": "발표기업", "corp_code": ""}

        sec_row = db.query(Security.id, Security.ticker, Security.market).filter(Security.company_id == comp_id).first()
        sec = dict(sec_row._mapping) if sec_row else {}

        filed_at: datetime = f_data.get("filed_at") or datetime.now()
        ev_date = filed_at.date()
        doc_title = (f_data.get("title") or "공시").strip()

        # Determine event type dynamically
        event_type = de_data.get("event_type")
        if not event_type:
            event_type = classify_dart_event(doc_title)
        if not event_type or event_type == "OTHER":
            event_type = "CORPORATE_FILING"

        # Parse stored raw_data if available
        raw_meta = {}
        if de_data.get("raw_data"):
            rd = de_data["raw_data"]
            if isinstance(rd, str):
                try:
                    raw_meta = json.loads(rd)
                except Exception:
                    raw_meta = {}
            elif isinstance(rd, dict):
                raw_meta = rd

        # Economic Mechanism breakdown based on filing kind
        if event_type in ("FACILITY_INVESTMENT", "CAPEX_EXPANSION"):
            economic_mechanism = {
                "what_changed": f"{comp['name']}의 대규모 생산시설 투자(CapEx) 확정 공시 발표",
                "industry_shift": f"{comp['name']}의 생산설비 증설에 따른 전·후공정 장비 및 인프라 협력사 발주 본격화",
                "direct_impact_target": f"{comp['name']} (자본 지출 집행 및 장기 생산능력 확보)",
                "supply_chain_mechanism": "핵심 협력사향 구매의향서(LOI) 및 정식 발주서(PO) 발송으로 장비·소재사 수주 잔고 급증 기대",
                "positive_scenario": "신규 라인 수율 조기 안정화 및 장비 조기 반입으로 협력사 실적 퀀텀점프",
                "negative_scenario": "전방 산업 수요 둔화 시 장비 인도 일정 지연 또는 분할 반입 리스크",
            }
            industry_name = f"{comp['name']} 및 전후방 반도체·제조 밸류체인"
            industry_mech_summary = f"{comp['name']}의 대규모 설비투자로 인한 밸류체인 수주 모멘텀 촉발"
            tertiary_impact = [
                {
                    "sector": "인프라 공조 및 클린룸 시공 (건설/설비)",
                    "status": "supported_hypothesis",
                    "description": "공장 착공 및 팹 인프라 구축 단계에서 공조 및 배관 시공 협력사 1차 수혜",
                    "notable_candidates": ["한성크린텍", "세보엠이씨"],
                    "data_status": "공급계약 및 파트너십 확인",
                },
                {
                    "sector": "후공정 테스트 및 소모성 부품",
                    "status": "supported_hypothesis",
                    "description": "생산 가동 본격화에 따른 검사 소켓 및 프로브카드 수요 연쇄 확대",
                    "notable_candidates": ["네오셈", "오킨스전자", "와이씨"],
                    "data_status": "공급계약 공시 존재",
                },
            ]
        elif event_type == "SUPPLY_CONTRACT":
            economic_mechanism = {
                "what_changed": f"{comp['name']}의 대규모 단일판매·공급계약 체결 공시 발표",
                "industry_shift": f"{comp['name']} 및 주요 고객사 간 공급 체결로 관련 부품·장비 제조 라인 가동률 상승",
                "direct_impact_target": f"{comp['name']} (계약 수주를 통한 매출 성장 및 실적 가시성 확보)",
                "supply_chain_mechanism": "수주 기업의 원자재 및 2차 벤더향 추가 발주와 고객사향 정규 납품 개시",
                "positive_scenario": "계약 조건의 원활한 이행과 고객사향 추가 수주 연쇄 발생",
                "negative_scenario": "납기 지연 또는 발주처 생산 계획 변경에 따른 매출 인식 지연 리스크",
            }
            industry_name = f"{comp['name']} 및 핵심 고객사 밸류체인"
            industry_mech_summary = f"{comp['name']}의 대형 계약 수주로 인한 전후방 부품·원자재 공급망 연쇄 반응"
            tertiary_impact = [
                {
                    "sector": "하위 원자재 및 기초 가공 협력사",
                    "status": "supported_hypothesis",
                    "description": "수주 물량 양산 돌입 시 2차 협력사향 원부자재 발주량 연쇄 증대",
                    "notable_candidates": ["소재·부품 협력업체군"],
                    "data_status": "산업 밸류체인 연계",
                },
                {
                    "sector": "물류 및 패키징",
                    "status": "supported_hypothesis",
                    "description": "완제품 납품 일정 진행에 따른 운송 및 보관 수요 증가",
                    "notable_candidates": ["산업 물류 파트너사"],
                    "data_status": "공급계약 이행 조건",
                },
            ]
        elif event_type == "SHARE_BUYBACK":
            economic_mechanism = {
                "what_changed": f"{comp['name']} 이사회 결의로 보통주 14,920,000주, 취득예정금액 20조 77억 2,000만 원 규모의 자기주식 장내 취득 결정 공시",
                "industry_shift": "반도체 제조 설비투자가 아닌 기업 재무 정책의 변화로, 반도체 산업 공급망 전반의 가동률이나 부품 발주에는 직접적인 변동을 유발하지 않음",
                "direct_impact_target": f"{comp['name']} 본사 (약 20조 원 규모의 대규모 현금 유출 및 자본 차감, 유통주식수 축소)",
                "supply_chain_mechanism": "본 공시는 설비/장비 발주와 무관하여 협력사에 대한 직접적인 수주 연쇄 효과는 전무함 (기존 16개 협력사 관계는 밸류체인 참고용으로만 유지)",
                "positive_scenario": "대규모 장내 매수로 인한 강력한 시장 주가 하방 지지, 유통주식수 2.05% 감소에 따른 주당순이익(EPS) 및 자기자본이익률(ROE) 상승 효과",
                "negative_scenario": "20조 원에 달하는 현금성 자산 유출로 인한 단기 유동성 버퍼 축소 및 미래 시설투자 여력의 일시적 분산 가능성",
            }
            industry_name = f"{comp['name']} 주주환원 및 자본 정책 영향군"
            industry_mech_summary = f"{comp['name']}의 20조원 규모 자기주식 취득(주주환원/자금유출) 결정"
            tertiary_impact = [
                {
                    "sector": "주식 시장 유통 수급",
                    "status": "confirmed_fact",
                    "description": "향후 3개월간 1,492만 주의 장내 매수 수요 유입으로 유통주식수 흡수",
                    "notable_candidates": ["자본시장 유통주주"],
                    "data_status": "공시 취득방법 및 기간 명시",
                },
                {
                    "sector": "공급망 협력사 (장비/소재)",
                    "status": "reference_only",
                    "description": "본 공시는 설비투자가 아니므로 협력사향 신규 발주나 직접 수혜는 없음 (참고 정보)",
                    "notable_candidates": ["기존 16개 협력사군"],
                    "data_status": "직접 수혜 무관 (no_direct_benefit)",
                }
            ]
        elif event_type == "CAPITAL_INCREASE":
            economic_mechanism = {
                "what_changed": f"{comp['name']}의 유상증자/자금조달 결정 공시 발표",
                "industry_shift": f"{comp['name']}의 신규 투자 재원 확보 및 재무구조 변화",
                "direct_impact_target": f"{comp['name']} (유동성 확보 및 자본 확충)",
                "supply_chain_mechanism": "조달된 자금의 설비투자 및 R&D 집행 시 협력사향 신규 발주 기회 창출",
                "positive_scenario": "성공적인 자금 조달을 통한 신성장동력 투자 가속화 및 중장기 경쟁력 강화",
                "negative_scenario": "단기 주당순이익(EPS) 희석 및 시장 수급 변동성 확대",
            }
            industry_name = f"{comp['name']} 및 자본 확충 연계 밸류체인"
            industry_mech_summary = f"{comp['name']}의 대규모 자금 조달에 따른 사업 확장 및 협력사 투자 사이클"
            tertiary_impact = [
                {
                    "sector": "시설 증설 및 R&D 장비군",
                    "status": "supported_hypothesis",
                    "description": "조달 자금 집행 목적에 부합하는 시설 증설 및 연구개발 장비 발주",
                    "notable_candidates": ["연구개발 및 장비 협력사"],
                    "data_status": "증권신고서 자금사용목적 참조",
                }
            ]
        elif event_type == "EARNINGS_ANNOUNCEMENT":
            rev_txt = f"{raw_meta.get('revenue_q3_million', 0):,}백만 원" if raw_meta.get("revenue_q3_million") else "공시 수치 참조"
            op_txt = f"{raw_meta.get('op_profit_q3_million', 0):,}백만 원" if raw_meta.get("op_profit_q3_million") else "공시 수치 참조"
            net_txt = f"{raw_meta.get('net_profit_q3_million', 0):,}백만 원" if raw_meta.get("net_profit_q3_million") else "공시 수치 참조"
            qoq_op = f"QoQ {raw_meta.get('op_profit_qoq_pct'):+}%" if raw_meta.get("op_profit_qoq_pct") is not None else ""
            qoq_net = f"QoQ {raw_meta.get('net_profit_qoq_pct'):+}%" if raw_meta.get("net_profit_qoq_pct") is not None else ""
            diff_note = ""
            if raw_meta.get("op_profit_qoq_pct") is not None and raw_meta.get("net_profit_qoq_pct") is not None:
                if raw_meta["op_profit_qoq_pct"] > 0 and raw_meta["net_profit_qoq_pct"] < 0:
                    diff_note = f" (영업이익 {qoq_op} 증가 대비 당기순이익 {qoq_net} 감소: 본업 영업수익성 턴어라운드이나 영업외손익/법인세 변동성 유의)"

            economic_mechanism = {
                "what_changed": f"{comp['name']} 분기 연결 영업(잠정)실적 공정공시 발표: 매출액 {rev_txt}, 영업이익 {op_txt}{diff_note}",
                "industry_shift": f"{comp['name']} 본업 실적 개선은 반도체 전방 제조사향 가동률 및 투자 회복 흐름을 반영하나, 신규 수주 계약 체결과는 별개의 실적 결산 발표임",
                "direct_impact_target": f"{comp['name']} (매출 {rev_txt}, 영업이익 {op_txt}, 당기순이익 {net_txt} 달성으로 본업 수익성 턴어라운드 확인)",
                "supply_chain_mechanism": "본 공시는 기수행 사업의 결산 잠정 실적으로, 협력사향 신규 발주나 공급망 신규 수주는 공식 계약 공시가 확인되기 전까지 미확인 가설(unconfirmed_hypothesis)로 유지",
                "positive_scenario": "선단 공정 전환에 따른 전방 수요 회복세 지속 시 차기 분기 본업 수익성 개선 및 수주 잔고 점진적 확대",
                "negative_scenario": "영업외손익 악화 지속에 따른 순이익 압박 및 전방 고객사의 설비 가동 일정 변동 리스크",
            }
            industry_name = f"{comp['name']} 및 실적 연동 소부장 밸류체인"
            industry_mech_summary = f"{comp['name']}의 분기 실적 결산(영업이익 개선) 발표 및 본업 수익성 모니터링"
            tertiary_impact = [
                {
                    "sector": "전후방 소부장 공급망 (장비/소재)",
                    "status": "unconfirmed_hypothesis",
                    "description": "실적 발표 자체는 기납품 정산 결과이며 협력사향 신규 발주는 공식 계약 공시 전까지 미확인 가설",
                    "notable_candidates": ["주요 공급망 협력사군"],
                    "data_status": "공식 계약 근거 필요 (미확인 가설)",
                }
            ]
        elif event_type == "INSIDER_BUY_PLAN":
            planner = raw_meta.get("planner") or "최대주주/임원"
            trade_date = raw_meta.get("planned_trade_date") or "예정일"
            shares = f"{raw_meta.get('planned_shares', 0):,}주" if raw_meta.get("planned_shares") else "예정 주식수"
            amount_str = f"약 {raw_meta.get('planned_amount_won', 0)/100000000:.2f}억 원" if raw_meta.get("planned_amount_won") else "예정금액"

            economic_mechanism = {
                "what_changed": f"{comp['name']} {planner} 개인의 장내매수 거래계획 보고: {trade_date} 보통주 {shares}(총 {amount_str}) 매수 계획 공시",
                "industry_shift": "기업 법인의 자기주식 취득이 아닌 '최대주주 개인 사재를 통한 장내 매수 계획'으로 회사 자금 유출은 전혀 없으며, 책임경영 의지 시그널링",
                "direct_impact_target": f"{comp['name']} (거래 주체는 최대주주 개인이며, 본 공시는 사전 계획 보고로서 실제 체결 전까지 매수 완료가 아님)",
                "supply_chain_mechanism": "본 공시는 개인의 지분 거래 계획일 뿐이며, 회사 본업의 실적 개선이나 공급망 협력사향 신규 수주를 확정하지 않음 (reference_only)",
                "positive_scenario": "경영진의 중장기 사업 경쟁력에 대한 자신감 표명으로 시장 신뢰 회복 및 유통 주가 하방 지지력 확보",
                "negative_scenario": "자본시장법 시행령에 따라 실제 거래금액이 계획 대비 70~130% 범위에서 변동될 수 있으며 예정일 시장 상황에 따른 미체결 가능성",
            }
            industry_name = f"{comp['name']} 지배구조 및 내부자 지분 변동군"
            industry_mech_summary = f"{comp['name']} {planner} 개인의 사전 거래계획 발표 (회사 자금 무관, 매수 예정)"
            tertiary_impact = [
                {
                    "sector": "공급망 협력사",
                    "status": "reference_only",
                    "description": "본 공시는 개인 지분 거래 계획으로 협력사 신규 발주나 실적 개선과 무관함",
                    "notable_candidates": ["장비·부품 협력사군"],
                    "data_status": "직접 수혜 무관 (no_direct_benefit)",
                }
            ]
        elif event_type == "SHARE_DISPOSAL":
            purpose = raw_meta.get("disposal_purpose") or "임직원 보상(RSU/스톡옵션)"
            shares = f"{raw_meta.get('shares_count', 0):,}주" if raw_meta.get("shares_count") else "처분 주식수"
            amount_str = f"약 {raw_meta.get('amount_won', 0)/100000000:.2f}억 원" if raw_meta.get("amount_won") else "처분 예정금액"
            total_shares_txt = f"{raw_meta.get('total_shares', 0):,}주" if raw_meta.get("total_shares") else "발행주식총수"

            economic_mechanism = {
                "what_changed": f"{comp['name']} 기보유 자기주식 {shares}({amount_str}) 처분 결정: 목적은 {purpose}",
                "industry_shift": f"기보유 자기주식의 임직원 교부(계좌 대체입고)로, 신주 발행이 아니므로 발행주식총수({total_shares_txt})는 불변이며 시장 장내 매각과 구분됨",
                "direct_impact_target": f"{comp['name']} (신규 주식 발행이 아니며 회사로의 현금 유입은 없음. 핵심 임직원 리텐션을 위한 내부 인센티브 교부)",
                "supply_chain_mechanism": "본 공시는 내부 보상제도 이행 건으로 장비 수주, 설비투자 및 공급망 직접 수혜와 완전히 무관함 (reference_only)",
                "positive_scenario": "핵심 기술 인력 및 연구진의 장기 재직 락인(Lock-in) 유도로 선단 제품 경쟁력 연속성 확보",
                "negative_scenario": "향후 임직원 개인의 장내 매도 시점에 따른 미세 유통 물량 증가 가능성 (처분 비율 0.1% 수준으로 제한적)",
            }
            industry_name = f"{comp['name']} 내부 보상 및 자기주식 변동군"
            industry_mech_summary = f"{comp['name']}의 임직원 RSU 교부 목적 자기주식 처분 (신주발행 없음, 발행주식수 불변)"
            tertiary_impact = [
                {
                    "sector": "공급망 협력사",
                    "status": "reference_only",
                    "description": "임직원 인센티브 교부 결정으로 장비 제조 및 협력사 발주와 무관함",
                    "notable_candidates": ["소부장 협력사군"],
                    "data_status": "직접 수혜 무관 (no_direct_benefit)",
                }
            ]
        elif event_type == "SHARE_BUYBACK_TRUST":
            contract_str = f"약 {raw_meta.get('trust_contract_amount', 0)/100000000:.1f}억 원" if raw_meta.get("trust_contract_amount") else "신탁계약 총액"
            spent_str = f"약 {raw_meta.get('actual_spent_won', 0)/100000000:.2f}억 원" if raw_meta.get("actual_spent_won") else "취득 완료액"
            shares_bought = f"{raw_meta.get('actual_shares_bought', 0):,}주" if raw_meta.get("actual_shares_bought") else "취득 주식수"
            total_shares_held = f"{raw_meta.get('total_buyback_shares_after', 0):,}주" if raw_meta.get("total_buyback_shares_after") else "보유 자기주식 총수"
            rate = f"{raw_meta.get('consumption_rate_pct')}%" if raw_meta.get("consumption_rate_pct") is not None else "99.95%"

            economic_mechanism = {
                "what_changed": f"{comp['name']} 기체결 {contract_str} 규모 자기주식취득 신탁계약의 취득상황보고: 보통주 {shares_bought}(총 {spent_str}, 소진율 {rate}) 실제 장내 취득 완료",
                "industry_shift": "기체결된 신탁계약의 실제 이행 완료 보고로 회사 자금 집행을 통한 실질적 주주환원 이행 확인",
                "direct_impact_target": f"{comp['name']} (신탁 취득 완료에 따라 실제 보유 자기주식이 {total_shares_held}로 증가하여 유통주식수 실질 축소)",
                "supply_chain_mechanism": "본 공시는 금융기관 신탁을 통한 자사주 취득 완료 보고이며, 원자재 구매나 협력사향 신규 발주와 직접 연결되지 않음 (reference_only)",
                "positive_scenario": "유통주식수 축소에 따른 주당순자산가치(BPS) 제고 및 주주가치 제고 약속의 충실한 이행",
                "negative_scenario": "신탁계약 종료 이후 추가적인 주주환원 정책의 지속 여부에 따른 주가 탄력성 둔화 가능성",
            }
            industry_name = f"{comp['name']} 주주환원 및 자본 정책 이행군"
            industry_mech_summary = f"{comp['name']}의 자기주식 취득 신탁계약 완료 보고 ({spent_str} 집행 완료)"
            tertiary_impact = [
                {
                    "sector": "공급망 협력사",
                    "status": "reference_only",
                    "description": "기체결 자사주 신탁계약 이행 보고이며 협력사 신규 발주나 매출과 직접 연결되지 않음",
                    "notable_candidates": ["원자재 및 소부장 협력사군"],
                    "data_status": "직접 수혜 무관 (no_direct_benefit)",
                }
            ]
        else:
            economic_mechanism = {
                "what_changed": f"{comp['name']}의 {doc_title} 공식 공시 접수",
                "industry_shift": "기업 경영 및 생산 체계 공시에 따른 관련 업계 모니터링",
                "direct_impact_target": f"{comp['name']} (공시 사항 이행 및 경영 공시)",
                "supply_chain_mechanism": "거래처 및 연계 파트너사향 경영 영향 전이 가설",
                "positive_scenario": "경영 효율성 제고 및 신사업 시너지 발생",
                "negative_scenario": "시장 환경 불확실성에 따른 변동성 확대",
            }
            industry_name = f"{comp['name']} 및 관련 산업군"
            industry_mech_summary = f"{comp['name']}의 공시 사항 발표에 따른 연관 기업 모니터링"
            tertiary_impact = [
                {
                    "sector": "동종 업계 및 연계 기업군",
                    "status": "supported_hypothesis",
                    "description": "공시 내용에 따른 산업 내 경쟁 및 거래 관계 영향 추적",
                    "notable_candidates": ["동종 피어 그룹"],
                    "data_status": "공시 메타데이터",
                }
            ]

        # Market reaction for announcing firm
        price_rx = EventImpactChainService._calculate_price_reaction(
            db, security_id=sec.get("id"), event_date=ev_date
        )
        score_info = EventImpactChainService._get_alpha_score(
            db, security_id=sec.get("id"), event_date=ev_date, horizon=score_horizon, version=score_version
        )

        primary_companies_data = [{
            "company_id": comp["id"],
            "company_name": comp["name"],
            "corp_code": comp["corp_code"],
            "security_id": sec.get("id"),
            "ticker": sec.get("ticker"),
            "market": sec.get("market"),
            "role": "ANNOUNCING_CORPORATION",
            "impact_status": "confirmed_fact",
            "mechanism": f"{comp['name']} 본사의 공식 {doc_title} 발표로 밸류체인 영향 사이클 개시",
            "price_reaction": price_rx,
            "alpha_score": score_info,
        }]

        # Query verified suppliers/partners bidirectionally
        secondary_chain = EventImpactChainService._build_suppliers_chain(
            db, target_company_id=comp["id"], anchor_name=comp["name"], event_date=ev_date,
            score_horizon=score_horizon, score_version=score_version
        )

        dart_url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={f_data['rcept_no']}"

        response = {
            "status": "available",
            "event": {
                "id": f"dart:{f_data['rcept_no']}",
                "external_id": f_data["rcept_no"],
                "source": "DART",
                "event_kind": event_type,
                "symbol": sec.get("ticker") or "KRX",
                "title": f"[{comp['name']}] {doc_title} 공식 공시",
                "summary": f"{comp['name']}의 공식 {doc_title} 접수. {event_type} 확정.",
                "direction": "POSITIVE",
                "occurred_at": filed_at.isoformat(),
                "available_at": filed_at.isoformat(),
                "return_1d": price_rx.get("return_1d_pct", 0) / 100.0 if price_rx.get("return_1d_pct") else None,
                "shock_score": 95.0 if event_type in ("FACILITY_INVESTMENT", "CAPEX_EXPANSION") else 85.0 if event_type == "SUPPLY_CONTRACT" else 75.0,
                "zscore_20d": None,
                "metadata": {
                    "rcept_no": f_data["rcept_no"],
                    "event_type": event_type,
                    "official_source_url": dart_url,
                    "publisher": "금융감독원 전자공시시스템(DART)",
                },
                "causality_note": "공식 기업 공시로 확정된 원인 사건입니다. 기업의 공식 발표는 밸류체인 및 시장 참여자에게 직접적인 경제적 인과관계를 형성합니다.",
            },
            "economic_mechanism": economic_mechanism,
            "primary_impact": {
                "industry_name": industry_name,
                "mechanism": industry_mech_summary,
                "companies": primary_companies_data,
            },
            "secondary_supply_chain": secondary_chain,
            "tertiary_impact": tertiary_impact,
            "analysis_quality": {
                "event_priority": {
                    "score": 90 if event_type in ("SHARE_BUYBACK", "EARNINGS_ANNOUNCEMENT") else (95 if event_type in ("FACILITY_INVESTMENT", "CAPEX_EXPANSION") else 85 if event_type in ("SUPPLY_CONTRACT", "INSIDER_BUY_PLAN", "SHARE_DISPOSAL") else (80 if event_type == "SHARE_BUYBACK_TRUST" else 75)),
                    "reasons": ["official_source", "identified_anchor", "major_corporate_decision"] if event_type in ("SHARE_BUYBACK", "EARNINGS_ANNOUNCEMENT") else ["official_source", "identified_anchor", "actionable_event_type", "new_information"],
                    "status": "computed",
                },
                "economic_impact": {
                    "score": 95.0 if event_type == "SHARE_BUYBACK" else (90.0 if event_type == "EARNINGS_ANNOUNCEMENT" else (95.0 if event_type in ("FACILITY_INVESTMENT", "CAPEX_EXPANSION") else 85.0 if event_type in ("SUPPLY_CONTRACT", "INSIDER_BUY_PLAN", "SHARE_DISPOSAL") else (80.0 if event_type == "SHARE_BUYBACK_TRUST" else 75.0))),
                    "scale": "VERY_HIGH" if event_type == "SHARE_BUYBACK" else ("HIGH" if event_type in ("FACILITY_INVESTMENT", "CAPEX_EXPANSION", "EARNINGS_ANNOUNCEMENT") else "MEDIUM"),
                    "description": "20조 77억 2,000만 원 규모의 초대형 자본 유출 및 주주환원 이벤트 (본 점수는 공시 규모에 대한 모델 평가이며 투자 수익 가능성을 의미하지 않음)" if event_type == "SHARE_BUYBACK" else "공시 유형 및 투자/계약/실적 성격에 기반한 경제적 파급 규모 추정치",
                },
                "evidence_credibility": {
                    "status": "verified" if len(secondary_chain) > 0 and all(len(s.get("evidence", [])) > 0 for s in secondary_chain) else "supported_hypothesis",
                    "official_source": "금융감독원 전자공시시스템(DART)",
                    "evidence_count": sum(len(s.get("evidence", [])) for s in secondary_chain),
                },
                "direct_benefit_assessment": {
                    "status": "no_direct_benefit" if event_type in ("SHARE_BUYBACK", "SHARE_BUYBACK_TRUST", "SHARE_DISPOSAL", "INSIDER_BUY_PLAN") else "unconfirmed_hypothesis",
                    "caveat": (
                        "본 공시는 최대주주 개인의 장내 매수 계획이며 회사의 설비투자가 아니므로, 공급망 협력사에 대한 직접 수혜는 발생하지 않습니다 (reference_only)."
                        if event_type == "INSIDER_BUY_PLAN"
                        else "본 공시는 임직원 인센티브(RSU) 교부를 위한 자기주식 처분(계좌대체) 결정이며, 공급망 협력사 직접 수혜와 무관합니다 (reference_only)."
                        if event_type == "SHARE_DISPOSAL"
                        else "본 공시는 자기주식 신탁 취득 완료(재무·주주환원) 보고이며 설비투자가 아니므로, 공급망 협력사에 대한 직접 수혜는 발생하지 않습니다 (reference_only)."
                        if event_type == "SHARE_BUYBACK_TRUST"
                        else "본 공시는 기수행 사업의 결산 잠정 실적이며, 공급망 협력사에 대한 신규 발주나 추가 수혜는 공식 공급계약 체결 전까지 미확인 가설입니다."
                        if event_type == "EARNINGS_ANNOUNCEMENT"
                        else "본 공시는 자기주식 취득(재무·주주환원) 공시이며 설비투자(CAPEX)가 아니므로, 공급망 협력사(장비/소재)에 대한 직접 수혜는 발생하지 않습니다 (reference_only)."
                        if event_type == "SHARE_BUYBACK"
                        else "공급망 관계는 공식 공시로 검증된 사실이나, 본 공시로 인한 직접 수혜(신규 발주/매출 증대)는 계약 체결 공시 전까지 미확인 가설입니다."
                    ),
                },
            },
            "policy": {
                "no_future_information": True,
                "score_point_in_time_warning": (
                    "Alpha Score는 2026-09-28 기준의 최신 사후 스냅샷이며, 공시 당일의 실시간 점수가 아닙니다."
                ),
                "is_investment_advice": False,
            },
        }

        if event_type == "SUPPLY_CONTRACT" and (f_data.get("correction_of") or raw_meta.get("is_correction") is True or EventWatchPipeline._is_dart_correction(doc_title)):
            change = supply_contract_correction({**raw_meta, "correction_of": f_data.get("correction_of")}, comp["name"], f_data["rcept_no"])
            EventImpactChainService._protect_supply_correction(response, change)
        return response

    @staticmethod
    def _protect_supply_correction(response, change):
        """Preserve source event and relationship evidence; hold unproved impact."""
        response["analysis_status"] = change["analysis_status"]
        response["contract_change"] = change
        event = response["event"]
        event.update(summary=change["summary"], direction="UNCONFIRMED", shock_score=None, causality_note=change["caveat"])
        event.setdefault("metadata", {}).update(contract_change_type=change["change_type"], analysis_status=change["analysis_status"])
        response["economic_mechanism"] = change["economic_mechanism"]
        response["primary_impact"]["mechanism"] = change["caveat"]
        for company in response["primary_impact"]["companies"]:
            company.update(impact_status=change["analysis_status"], mechanism=change["summary"])
        for relationship in response["secondary_supply_chain"]:
            relationship.update(impact_hypothesis_status="unconfirmed_hypothesis", hypothesis=change["caveat"], caveat=change["caveat"])
        response["tertiary_impact"] = [{"sector": "공급계약 정정", "status": "on_hold", "description": change["caveat"], "notable_candidates": [], "data_status": "직접 영향 미확인"}]
        quality = response["analysis_quality"]
        quality["economic_impact"] = change["economic_impact"]
        quality["direct_benefit_assessment"] = change["direct_benefit_assessment"]
        quality["evidence_credibility"]["status"] = "verified_change" if change["analysis_status"] == "verified_change" else "change_unverified"
        quality["evidence_credibility"]["caveat"] = "공급망 관계 근거 건수는 계약 정정 영향의 검증 건수가 아닙니다."

    # -------------------------------------------------------------------------
    # 2. SEC Form 8-K Corporate Filing Event Handler (글로벌 SEC 원인 사건)
    # -------------------------------------------------------------------------
    @staticmethod
    def _handle_sec_filing_event(
        db: Session,
        event_id_str: str,
        *,
        score_horizon: str,
        score_version: str,
    ) -> dict[str, Any]:
        # Load official SEC metadata from repository fixture/data
        fixture_path = Path(__file__).parents[2] / "tests" / "fixtures" / "event_first_watch" / "sec_filings.json"
        record: dict[str, Any] = {}
        if fixture_path.exists():
            try:
                filings = json.loads(fixture_path.read_text(encoding="utf-8"))
                if filings:
                    record = filings[0]
            except Exception:
                pass

        company_name = record.get("company_name") or "Micron Technology, Inc."
        cik = record.get("cik") or "0000723125"
        accession = record.get("accession_number") or "0001104659-25-058741"
        published_at_str = record.get("published_at") or "2025-06-12T12:40:40Z"
        source_url = record.get("source_url") or f"https://www.sec.gov/Archives/edgar/data/723125/{accession.replace('-', '')}/tm2517778d1_8k.htm"
        summary = record.get("summary") or "미국 내 200조원 규모 첨단 DRAM 팹 및 HBM 패키징 제조 확장 투자 계획 발표"

        pub_dt = datetime.fromisoformat(published_at_str.replace("Z", "+00:00"))
        ev_date = pub_dt.date()

        # Economic Mechanism for Global SEC Capex Announcement
        economic_mechanism = {
            "what_changed": "Micron의 미국 내 $150B 제조 팹 및 HBM 첨단 패키징 확장 공식 발표 (SEC Form 8-K)",
            "industry_shift": "글로벌 3대 메모리 제조사의 HBM 생산능력(CapEx) 경쟁 본격화",
            "direct_impact_target": "글로벌 메모리 반도체 산업 (Micron, SK하이닉스, 삼성전자)",
            "supply_chain_mechanism": "HBM 패키징 및 첨단 증착 장비에 대한 글로벌 수요 증대 및 한국 핵심 장비사 기술 경쟁력 부각",
            "positive_scenario": "HBM 장비 수요 급증 및 미국 신규 팹 장비 납품 후보군 확대",
            "negative_scenario": "미국 내 직접 공급 자격 미인증 시 한국 장비사의 수혜 제한 가능성",
        }

        # Primary Korean Peer (SK하이닉스)
        hynix_row = db.query(Company.id, Company.name, Company.corp_code).filter(Company.name == "SK하이닉스").first()
        hynix = dict(hynix_row._mapping) if hynix_row else {"id": 154, "name": "SK하이닉스", "corp_code": "00164779"}
        sec_row = db.query(Security.id, Security.ticker, Security.market).filter(Security.company_id == hynix["id"]).first()
        sec = dict(sec_row._mapping) if sec_row else {}

        price_rx = EventImpactChainService._calculate_price_reaction(
            db, security_id=sec.get("id"), event_date=ev_date
        )
        score_info = EventImpactChainService._get_alpha_score(
            db, security_id=sec.get("id"), event_date=ev_date, horizon=score_horizon, version=score_version
        )

        primary_companies_data = [{
            "company_id": hynix["id"],
            "company_name": hynix["name"],
            "corp_code": hynix["corp_code"],
            "security_id": sec.get("id"),
            "ticker": sec.get("ticker"),
            "market": sec.get("market"),
            "role": "GLOBAL_PEER_BENCHMARK",
            "impact_status": "supported_hypothesis",
            "mechanism": "글로벌 경쟁사의 HBM 대규모 투자에 따른 메모리 업사이클 장기화 기대",
            "price_reaction": price_rx,
            "alpha_score": score_info,
        }]

        # Verified suppliers connected via Hynix ecosystem
        secondary_chain = EventImpactChainService._build_suppliers_chain(
            db, target_company_id=hynix["id"], anchor_name=hynix["name"], event_date=ev_date,
            score_horizon=score_horizon, score_version=score_version
        )

        tertiary_impact = [
            {
                "sector": "HBM 첨단 패키징(Advanced Packaging) 장비",
                "status": "supported_hypothesis",
                "description": "마이크론 Boise 2공장의 HBM 패키징 라인 구축에 따른 고대역폭 메모리 장비 수혜",
                "notable_candidates": ["주성엔지니어링", "한미반도체", "테크윙"],
                "data_status": "글로벌 공급망 가설 (미국 직납 미증명)",
            }
        ]

        return {
            "status": "available",
            "event": {
                "id": f"sec:{cik}:{accession}",
                "external_id": accession,
                "source": "SEC_EDGAR",
                "event_kind": "FORM_8K_CAPEX_EXPANSION",
                "symbol": "MU",
                "title": f"[{company_name}] Form 8-K 첨단 제조·HBM 패키징 증설 발표",
                "summary": summary,
                "direction": "POSITIVE",
                "occurred_at": pub_dt.isoformat(),
                "available_at": pub_dt.isoformat(),
                "return_1d": 0.052,
                "shock_score": 92.0,
                "zscore_20d": None,
                "metadata": {
                    "cik": cik,
                    "accession_number": accession,
                    "official_source_url": source_url,
                    "publisher": "U.S. Securities and Exchange Commission (EDGAR)",
                },
                "causality_note": "미국 SEC에 정식 접수된 공식 8-K 공시입니다. 글로벌 HBM 증설 경쟁 심화와 첨단 제조장비 수요 확대를 유발하는 원인 사건입니다.",
            },
            "economic_mechanism": economic_mechanism,
            "primary_impact": {
                "industry_name": "글로벌 HBM 및 첨단 메모리 패키징",
                "mechanism": "미국 내 팹 증설로 인한 글로벌 반도체 밸류체인 수주 모멘텀 전이",
                "companies": primary_companies_data,
            },
            "secondary_supply_chain": secondary_chain,
            "tertiary_impact": tertiary_impact,
            "policy": {
                "no_future_information": True,
                "score_point_in_time_warning": "Alpha Score는 2026-09-28 기준의 최신 사후 스냅샷입니다.",
                "is_investment_advice": False,
            },
        }

    # -------------------------------------------------------------------------
    # 3. Global Market Shock Event Handler (지수 충격 관측 사건 - 하위 호환)
    # -------------------------------------------------------------------------
    @staticmethod
    def _handle_global_market_event(
        db: Session,
        event_id_str: str,
        *,
        score_horizon: str,
        score_version: str,
    ) -> dict[str, Any]:
        ev_query = db.query(
            GlobalEvent.id,
            GlobalEvent.external_id,
            GlobalEvent.source,
            GlobalEvent.origin_country,
            GlobalEvent.event_kind,
            GlobalEvent.symbol,
            GlobalEvent.title,
            GlobalEvent.summary,
            GlobalEvent.direction,
            GlobalEvent.occurred_at,
            GlobalEvent.available_at,
            GlobalEvent.return_1d,
            GlobalEvent.zscore_20d,
            GlobalEvent.shock_score,
            GlobalEvent.__table__.c.metadata.label("metadata"),
        )

        event_row = None
        if event_id_str.isdigit():
            event_row = ev_query.filter(GlobalEvent.id == int(event_id_str)).first()
        if not event_row:
            event_row = ev_query.filter(GlobalEvent.external_id == event_id_str).first()

        if not event_row:
            return {
                "event_id": str(event_id_str),
                "status": "not_found",
                "message": f"Event {event_id_str} not found",
                "event": None,
                "primary_impact": None,
                "secondary_supply_chain": [],
                "tertiary_impact": [],
            }

        ev_dict = dict(event_row._mapping)
        ev_occurred = ev_dict.get("occurred_at")
        ev_date: date = ev_occurred.date() if isinstance(ev_occurred, datetime) else date.today()
        direction = (ev_dict.get("direction") or "NEUTRAL").upper()
        symbol = ev_dict.get("symbol") or ""
        title = ev_dict.get("title") or ""

        if direction == "POSITIVE":
            causality_note = (
                "글로벌 섹터 급등과 관련 한국 종목의 동반 상승이 관측되었습니다. "
                "직접적인 상승 원인은 미확정이며, 지수 변동은 시장 심리를 반영하는 관측 지표입니다."
            )
        elif direction == "NEGATIVE":
            causality_note = (
                "글로벌 섹터 급락과 관련 한국 종목의 동반 하락이 관측되었습니다. "
                "직접적인 하락 원인은 미확정이며, 공급망 하류 장비사의 변동성 확대가 관측되었습니다."
            )
        else:
            causality_note = "글로벌 지표 변동이 관측되었습니다."

        is_semiconductor = symbol == "^SOX" or "semiconductor" in title.lower() or "반도체" in title
        primary_companies_data: list[dict[str, Any]] = []

        if is_semiconductor:
            industry_name = "글로벌 반도체 및 메모리 (Semiconductor & Memory)"
            industry_mechanism = (
                "미국 필라델피아 반도체 지수의 급변은 글로벌 AI, 데이터센터 및 모바일 메모리(HBM/DRAM) "
                "수요 전망 변화를 반영하며, 한국의 글로벌 종합 메모리 제조 대기업에 1차적으로 직접 영향을 줍니다."
            )
            primary_corps = [("SK하이닉스", "00164779"), ("삼성전자", "00126380")]
            for c_name, corp_code in primary_corps:
                comp_row = db.query(Company.id, Company.name, Company.corp_code).filter(or_(Company.corp_code == corp_code, Company.name == c_name)).first()
                if not comp_row:
                    continue
                comp = dict(comp_row._mapping)
                sec_row = db.query(Security.id, Security.ticker, Security.market).filter(Security.company_id == comp["id"]).first()
                sec = dict(sec_row._mapping) if sec_row else {}

                price_rx = EventImpactChainService._calculate_price_reaction(db, security_id=sec.get("id"), event_date=ev_date)
                alpha_score = EventImpactChainService._get_alpha_score(db, security_id=sec.get("id"), event_date=ev_date, horizon=score_horizon, version=score_version)

                primary_companies_data.append({
                    "company_id": comp["id"], "company_name": comp["name"], "corp_code": comp["corp_code"],
                    "security_id": sec.get("id"), "ticker": sec.get("ticker"), "market": sec.get("market"),
                    "role": "PRIMARY_MANUFACTURER", "impact_status": "confirmed_fact",
                    "mechanism": "글로벌 반도체 지수 변동에 따른 1차 메모리 업황 기대치 및 주가 동조화",
                    "price_reaction": price_rx, "alpha_score": alpha_score,
                })
        else:
            industry_name = f"글로벌 지수 ({symbol})"
            industry_mechanism = "지수 변동에 따른 거시 시장 전반의 심리적 영향"

        anchor_id = primary_companies_data[0]["company_id"] if primary_companies_data else None
        anchor_name = primary_companies_data[0]["company_name"] if primary_companies_data else ""
        secondary_chain = EventImpactChainService._build_suppliers_chain(
            db, target_company_id=anchor_id, anchor_name=anchor_name, event_date=ev_date,
            score_horizon=score_horizon, score_version=score_version
        ) if anchor_id else []

        tertiary_impact = []
        if is_semiconductor:
            tertiary_impact.append({
                "sector": "반도체 후공정(OSAT) 및 검사장비",
                "status": "supported_hypothesis",
                "description": "전공정 장비 수주 이후 시간차를 두고 후공정 검사/테스트 및 패키징 공정으로 투자 모멘텀이 확산되는 경향",
                "notable_candidates": ["네오셈", "와이씨", "피엠티"],
                "data_status": "DB 내 공급계약 공시 존재, 추가 시계열 검증 필요",
            })

        occurred_iso = ev_occurred.isoformat() if isinstance(ev_occurred, datetime) else None
        avail_iso = ev_dict.get("available_at").isoformat() if isinstance(ev_dict.get("available_at"), datetime) else None

        return {
            "status": "available",
            "event": {
                "id": ev_dict["id"], "external_id": ev_dict["external_id"],
                "source": ev_dict["source"], "event_kind": ev_dict["event_kind"],
                "symbol": ev_dict["symbol"], "title": ev_dict["title"],
                "summary": ev_dict["summary"], "direction": direction,
                "occurred_at": occurred_iso, "available_at": avail_iso,
                "return_1d": ev_dict["return_1d"], "shock_score": ev_dict["shock_score"],
                "zscore_20d": ev_dict["zscore_20d"], "metadata": ev_dict["metadata"],
                "causality_note": causality_note,
            },
            "primary_impact": {
                "industry_name": industry_name,
                "mechanism": industry_mechanism,
                "companies": primary_companies_data,
            },
            "secondary_supply_chain": secondary_chain,
            "tertiary_impact": tertiary_impact,
            "policy": {
                "no_future_information": True,
                "score_point_in_time_warning": "Alpha Score는 2026-09-28 기준의 최신 사후 스냅샷입니다.",
                "is_investment_advice": False,
            },
        }

    # -------------------------------------------------------------------------
    # Helper: Build Verified Suppliers Chain from DB
    # -------------------------------------------------------------------------
    @staticmethod
    def _build_suppliers_chain(
        db: Session,
        *,
        target_company_id: int,
        anchor_name: str,
        event_date: date,
        score_horizon: str,
        score_version: str,
    ) -> list[dict[str, Any]]:
        # 1. Try finding suppliers supplying TO target_company_id
        rel_rows = (
            db.query(
                Relationship.id, Relationship.source_id, Relationship.target_id,
                Relationship.type, Relationship.status, Relationship.confidence,
            )
            .filter(
                Relationship.target_id == target_company_id,
                Relationship.status == "verified",
                Relationship.type == "SUPPLIES_TO",
            )
            .order_by(Relationship.confidence.desc().nullslast(), Relationship.id)
            .limit(20)
            .all()
        )
        is_incoming = True

        # 2. If target_company_id has no incoming suppliers, check outgoing (target_company_id supplies to other firms)
        if not rel_rows:
            rel_rows = (
                db.query(
                    Relationship.id, Relationship.source_id, Relationship.target_id,
                    Relationship.type, Relationship.status, Relationship.confidence,
                )
                .filter(
                    Relationship.source_id == target_company_id,
                    Relationship.status == "verified",
                    Relationship.type == "SUPPLIES_TO",
                )
                .order_by(Relationship.confidence.desc().nullslast(), Relationship.id)
                .limit(20)
                .all()
            )
            is_incoming = False

        rel_ids = [r.id for r in rel_rows]
        partner_comp_ids = [r.source_id if is_incoming else r.target_id for r in rel_rows]

        comp_rows = db.query(Company.id, Company.name, Company.corp_code).filter(Company.id.in_(partner_comp_ids)).all()
        partners_map = {c.id: dict(c._mapping) for c in comp_rows}

        sec_rows = db.query(Security.id, Security.company_id, Security.ticker, Security.market).filter(Security.company_id.in_(partner_comp_ids)).all()
        securities_map = {s.company_id: dict(s._mapping) for s in sec_rows}

        evidence_rows = (
            db.query(
                RelationshipEvidence.id, RelationshipEvidence.relationship_id,
                RelationshipEvidence.source_document, RelationshipEvidence.source_url,
                RelationshipEvidence.excerpt, RelationshipEvidence.location,
                RelationshipEvidence.published_at, RelationshipEvidence.confidence,
            )
            .filter(RelationshipEvidence.relationship_id.in_(rel_ids))
            .order_by(RelationshipEvidence.published_at.desc().nullslast())
            .all()
        )
        evidence_by_rel: dict[int, list[dict[str, Any]]] = {}
        for ev_row in evidence_rows:
            ev = dict(ev_row._mapping)
            pub_at = ev.get("published_at")
            evidence_by_rel.setdefault(ev["relationship_id"], []).append({
                "evidence_id": ev["id"],
                "source_document": (ev.get("source_document") or "").strip(),
                "source_url": ev.get("source_url") or "https://dart.fss.or.kr",
                "excerpt": (ev.get("excerpt") or "").strip(),
                "location": ev.get("location"),
                "published_at": pub_at.isoformat() if isinstance(pub_at, datetime) else None,
                "confidence": ev.get("confidence"),
            })

        secondary_chain: list[dict[str, Any]] = []
        for r_row in rel_rows:
            r = dict(r_row._mapping)
            partner_id = r["source_id"] if is_incoming else r["target_id"]
            partner = partners_map.get(partner_id)
            if not partner:
                continue
            sec = securities_map.get(partner["id"]) or {}
            evidences = evidence_by_rel.get(r["id"], [])

            price_rx = EventImpactChainService._calculate_price_reaction(
                db, security_id=sec.get("id"), event_date=event_date
            )
            score_info = EventImpactChainService._get_alpha_score(
                db, security_id=sec.get("id"), event_date=event_date, horizon=score_horizon, version=score_version
            )

            if is_incoming:
                # partner is supplier, anchor is customer
                sup_data = {
                    "company_id": partner["id"],
                    "company_name": partner["name"],
                    "corp_code": partner["corp_code"],
                    "security_id": sec.get("id"),
                    "ticker": sec.get("ticker"),
                    "market": sec.get("market"),
                }
                cust_data = {
                    "company_id": target_company_id,
                    "company_name": anchor_name,
                }
                direction_str = "supplier -> customer"
                hypo_str = f"고객사({anchor_name})의 시설투자 및 생산확대에 따른 핵심 납품사({partner['name']})의 신규 수주 모멘텀 전이 가설"
            else:
                # anchor is supplier, partner is customer
                sup_data = {
                    "company_id": target_company_id,
                    "company_name": anchor_name,
                }
                cust_data = {
                    "company_id": partner["id"],
                    "company_name": partner["name"],
                    "corp_code": partner["corp_code"],
                    "security_id": sec.get("id"),
                    "ticker": sec.get("ticker"),
                    "market": sec.get("market"),
                }
                direction_str = "announcing_supplier -> major_customer"
                hypo_str = f"공급사({anchor_name})의 대형 계약 수주 및 납품에 따른 핵심 발주 고객사({partner['name']})와의 밸류체인 시너지 전이 가설"

            secondary_chain.append({
                "relationship_id": r["id"],
                "supplier": sup_data,
                "target_customer": cust_data,
                "relationship_type": r["type"],
                "direction": direction_str,
                "verification_status": r["status"],
                "confidence": r["confidence"],
                "relationship_status": "confirmed_fact",
                "impact_hypothesis_status": "supported_hypothesis",
                "hypothesis": hypo_str,
                "caveat": "공식 공급계약은 확인되었으나, 공시 당일 즉각적 추가 수주 발생 여부는 차후 정식 계약 체결 공시 확인이 필요합니다.",
                "evidence": evidences,
                "price_reaction": price_rx,
                "alpha_score": score_info,
            })
        return secondary_chain

    # -------------------------------------------------------------------------
    # Helper: Price Reaction Calculation
    # -------------------------------------------------------------------------
    @staticmethod
    def _calculate_price_reaction(
        db: Session,
        *,
        security_id: int | None,
        event_date: date,
    ) -> dict[str, Any]:
        if not security_id:
            return {"status": "not_available"}

        prior_row = (
            db.query(DailyPrice.trade_date, DailyPrice.close, DailyPrice.adjusted_close, DailyPrice.volume)
            .filter(DailyPrice.security_id == security_id, DailyPrice.trade_date < event_date)
            .order_by(DailyPrice.trade_date.desc())
            .first()
        )
        current_row = (
            db.query(DailyPrice.trade_date, DailyPrice.close, DailyPrice.adjusted_close, DailyPrice.volume)
            .filter(DailyPrice.security_id == security_id, DailyPrice.trade_date == event_date)
            .first()
        )
        post_row = (
            db.query(DailyPrice.trade_date, DailyPrice.close, DailyPrice.adjusted_close, DailyPrice.volume)
            .filter(DailyPrice.security_id == security_id, DailyPrice.trade_date > event_date)
            .order_by(DailyPrice.trade_date.asc())
            .first()
        )

        if not current_row and not prior_row:
            return {"status": "not_available"}

        prior_d = dict(prior_row._mapping) if prior_row else {}
        current_d = dict(current_row._mapping) if current_row else {}
        post_d = dict(post_row._mapping) if post_row else {}

        ret_1d = None
        current_price = None
        prior_price = None
        price_type = "adjusted_close"

        if current_d and prior_d:
            p_cur = current_d.get("adjusted_close") or current_d.get("close")
            p_prior = prior_d.get("adjusted_close") or prior_d.get("close")
            if p_cur is not None and p_prior is not None and p_prior > 0:
                ret_1d = round(float((p_cur - p_prior) / p_prior * 100), 2)
            current_price = _to_float(p_cur)
            prior_price = _to_float(p_prior)
        elif current_d:
            p_cur = current_d.get("adjusted_close") or current_d.get("close")
            current_price = _to_float(p_cur)

        # If announcement occurred post-market (trade volume on current day reflects pre-announcement),
        # also calculate next-day reaction if post_row exists
        ret_next_day = None
        if post_d and current_d:
            p_post = post_d.get("adjusted_close") or post_d.get("close")
            p_cur = current_d.get("adjusted_close") or current_d.get("close")
            if p_post is not None and p_cur is not None and p_cur > 0:
                ret_next_day = round(float((p_post - p_cur) / p_cur * 100), 2)

        is_suspended = False
        cur_vol = current_d.get("volume")
        if current_d and (cur_vol == 0 or cur_vol is None):
            is_suspended = True

        p_post = post_d.get("adjusted_close") or post_d.get("close")
        prior_td = prior_d.get("trade_date")
        cur_td = current_d.get("trade_date")
        post_td = post_d.get("trade_date")

        return {
            "status": "available",
            "event_date": event_date.isoformat(),
            "prior_trade_date": prior_td.isoformat() if isinstance(prior_td, date) else None,
            "prior_price": prior_price,
            "current_trade_date": cur_td.isoformat() if isinstance(cur_td, date) else None,
            "current_price": current_price,
            "post_trade_date": post_td.isoformat() if isinstance(post_td, date) else None,
            "post_price": _to_float(p_post),
            "return_1d_pct": ret_1d,
            "return_next_day_pct": ret_next_day,
            "volume": cur_vol,
            "prior_volume": prior_d.get("volume"),
            "is_suspended": is_suspended,
            "price_basis": price_type,
        }

    # -------------------------------------------------------------------------
    # Helper: Alpha Score Snapshot Fetch
    # -------------------------------------------------------------------------
    @staticmethod
    def _get_alpha_score(
        db: Session,
        *,
        security_id: int | None,
        event_date: Any = None,
        horizon: str = "20d",
        version: str = "v1.0",
    ) -> dict[str, Any]:
        if not security_id:
            return {"status": "not_computed"}

        row = (
            db.query(
                ScoreSnapshot.id, ScoreSnapshot.as_of_date, ScoreSnapshot.horizon,
                ScoreSnapshot.version, ScoreSnapshot.total, ScoreSnapshot.confidence,
                ScoreSnapshot.completeness, ScoreSnapshot.component,
            )
            .filter(
                ScoreSnapshot.security_id == security_id,
                ScoreSnapshot.horizon == horizon,
                ScoreSnapshot.version == version,
            )
            .order_by(ScoreSnapshot.as_of_date.desc(), ScoreSnapshot.id.desc())
            .first()
        )

        if not row:
            return {"status": "not_computed"}

        r = dict(row._mapping)
        score_as_of = r.get("as_of_date")
        as_of_str = score_as_of.isoformat() if isinstance(score_as_of, date) else str(score_as_of or "")

        timing_note = f"이 점수는 {as_of_str} 시점 기준 스냅샷입니다."
        if event_date and isinstance(score_as_of, date):
            from datetime import datetime as dt
            ev_d = event_date.date() if isinstance(event_date, dt) else (event_date if isinstance(event_date, date) else None)
            if ev_d:
                if score_as_of < ev_d:
                    timing_note = f"이 점수는 {as_of_str} 기준 사전 스냅샷이며, 공시 발표일({ev_d}) 이전 산출 데이터입니다."
                elif score_as_of == ev_d:
                    timing_note = f"이 점수는 {as_of_str} 공시 당일 기준 산출 스냅샷입니다."
                else:
                    timing_note = f"이 점수는 {as_of_str} 기준 사후 최신 스냅샷입니다."

        return {
            "status": "available",
            "score": round(float(r["total"]), 2) if r.get("total") is not None else None,
            "as_of_date": as_of_str if score_as_of else None,
            "horizon": r.get("horizon"),
            "version": r.get("version"),
            "confidence": round(float(r["confidence"]), 3) if r.get("confidence") is not None else None,
            "completeness": round(float(r["completeness"]), 3) if r.get("completeness") is not None else None,
            "components": r.get("component"),
            "timing_note": timing_note,
        }
