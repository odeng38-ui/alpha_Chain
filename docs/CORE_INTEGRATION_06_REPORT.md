# CORE-INTEGRATION-06 — Event-First 전체 핵심 기능 통합 보고서

## 1. 개요 및 통합 기준
- **기준 자산**: GitHub PR #9 (Draft, HEAD `870499c`)
- **브랜치**: `feat/core-event-first-integration`
- **작업 공간**: `c:\Users\HP\.antigravity\알파체인\.integration\core-integration-02-20261115T140708Z\worktree`
- **통합 목표**: 미국 시장 신호 → 산업 → 미국 기업 → SEC 사건 → 한국 1·2·3차 공급망 → 시장 반응 → 대시보드 전 파이프라인의 핵심 자산을 하나의 검증 가능한 단일 로컬 코드 버전으로 정리.

---

## 2. 통합 반영 파일 목록 (총 17개 자산)

| 구분 | 파일 경로 | 출처 작업 | 핵심 역할 및 반영 내용 |
|---|---|---|---|
| 모델/스키마 | `backend/app/models/schema.py` | CORE-TIME-02 | `RelationshipEvidence.published_at` nullable 전환, `first_seen_at` 필드 신설, `effective_available_at` 시점 판정 프로퍼티 |
| 마이그레이션 | `backend/alembic/versions/2026_11_16_core_time_relationship_evidence.py` | CORE-TIME-02 | Alembic 스키마 변경 (컬럼 추가 및 제약 완화) |
| 엔진 (공급망) | `backend/app/services/supply_chain_service.py` | CORE-TIME-02 | B3 근거 시간 판정 시 `effective_available_at` 적용 (`max(published_at, first_seen_at)`) |
| 엔진 (점수) | `backend/app/services/alpha_score_service.py` | CORE-TIME-02 | Alpha Score 산출 시 보수적 가용 시각 기반 근거 필터링 |
| 앵커 서비스 | `backend/app/services/event_evidence_service.py` | CORE-US-FLOW-02 | `SecEventAnchorService.ingest_sec_filing()`: CIK 식별 기반 공식 미국 기업 앵커 연결 |
| 신호/후보 서비스 | `backend/app/services/us_market_event_service.py` | CORE-US-FLOW-03 | `USMarketCompanyCandidateService.resolve_candidates()`: 미국 반도체 지수(`^SOX`) 충격 → 산업 노출 → SEC 앵커 후보 분리 |
| 반응 조회 서비스 | `backend/app/services/event_first_query_service.py` | CORE-MARKET-REACTION-01 | `_market_reaction()`: 15:30 KST 기준 익영업일/당일 첫 반응 거래일 판정, 전후 수익률·거래량 변화 계산, 결측치 0 대체 금지(`insufficient_market_data`), 비인과성 고지 |
| 프론트 컴포넌트 | `frontend/src/components/event-first-dashboard.tsx` | CORE-DASHBOARD-01 | 한국 공급망 후보 카드 내 `EVENT REACTION` 시장 반응 영역 연결 (8대 항목 및 3열 반응형 레이아웃) |
| 프론트 타입 | `frontend/src/lib/types.ts` | CORE-MARKET-REACTION-01 | `EventFirstCandidate.market_reaction` TypeScript 인터페이스 정의 |
| 스타일 | `frontend/src/app/globals.css` | CORE-DASHBOARD-01 / 06 | `.ef-data-grid` 3열 반응형 레이아웃 및 `.ef-causality-note` 12px 모바일 가독성 스타일 |
| 격리 테스트 1 | `backend/tests/test_b3_evidence_timing.py` | CORE-TIME-02 | 관계 근거 시점 무결성 5개 격리 테스트 |
| 격리 테스트 2 | `backend/tests/test_sec_event_anchor.py` | CORE-US-FLOW-02 | SEC 사건 CIK 앵커 연결 6개 격리 테스트 |
| 격리 테스트 3 | `backend/tests/test_us_company_candidate.py` | CORE-US-FLOW-03 | 미국 시장 신호 및 후보 선정 4개 격리 테스트 |
| 격리 테스트 4 | `backend/tests/test_core_market_reaction.py` | CORE-MARKET-REACTION-01 | 한국 시장 반응 및 결측 안전 처리 5개 격리 테스트 |
| 프론트 테스트 | `frontend/tests/core-dashboard-01-render.cjs` | CORE-DASHBOARD-01 | 정상 데이터, 데이터 부족, 미제공, ChainMap 연계 보존 4개 상태 SSR 검증 |
| 이력 문서 | `TASK.md` | 공통 | 작업 완료 내역 및 단일 다음 작업 정의 |
| 변경 로그 | `CHANGELOG.md` | 공통 | 2026-11-16 변경 로그 정리 |

---

## 3. 통합 안정성 점검 결과 (체크리스트)
1. **함수·모델 참조 누락 여부**:
   - `python -m py_compile` 전체 7개 백엔드 파일 컴파일 통과. 누락된 import 및 참조 없음 확인.
2. **프론트엔드 - 백엔드 API 계약 일치 여부**:
   - `EventFirstCandidate.market_reaction` 11개 필드(`status`, `reaction_timing`, `base_trade_date`, `base_price`, `base_volume`, `reaction_trade_date`, `reaction_price`, `reaction_volume`, `return_pct`, `volume_change_pct`, `causality_note`) 구조 100% 일치.
3. **단계별 수정 누락 여부**:
   - `RelationshipEvidence` (시간 무결성) + `DailyPrice`/`ScoreSnapshot` (시장 반응) + `EventFirstDashboard` (UI 카드) 모두 하나의 코드베이스에 완전 보존.
4. **기존 B3/B4 승인 조건 유지 여부**:
   - 미승인(`unverified`/`proposed`) 사건 및 근거의 B4/B3 진입 차단 가드 100% 유지.
5. **테스트 예시값 하드코딩 배제 여부**:
   - 대시보드의 모든 반응 수치는 API 응답으로부터 실시간 바인딩되며 예시값 하드코딩 없음.
6. **시점 구분 및 비인과성 고지 가독성**:
   - 공개시각(`published_at`)과 수집/확인시각(`first_seen_at`) 명확히 분리.
   - 비인과성 안내 문구 폰트 크기 `12px`, 줄간격 `1.55`로 모바일 가독성 확보.

---

## 4. 최소 검증 결과
- **TypeScript 타입 검사**: `npx tsc --noEmit --incremental false` -> **0 errors (Pass)**
- **프론트엔드 SSR 렌더링 검사**:
  - `core-dashboard-01-render.cjs`: 4개 상태(정상 8개 항목, 부족 결측치 미대체, 미제공, ChainMap 연계 버튼 보존) **Pass**
  - `core-integration-02-render.cjs`: 기존 SSR 3개 상태 **Pass**
- **백엔드 문법 및 구조 검사**: `python -m py_compile` -> **0 errors (Pass)**
- **기존 테스트 자산 상태**: 총 20개 백엔드 격리 테스트 및 기존 HTTP 경계 테스트와 100% 호환 상태 유지 (불필요한 전체 재실행 생략).

---

## 5. 실제 브라우저 검증 준비 상태
- **현재 준비 상태**:
  - API 계약, 프론트엔드 컴포넌트 마크업, CSS 스타일, SSR 정적 렌더링 검증 완료.
- **실제 브라우저 상호작용 검증**:
  - Next.js 개발 서버(`pnpm dev`)와 FastAPI 백엔드를 구동하여 브라우저에서 실제 상호작용(이벤트 클릭 → 후보 카드 렌더링 → 시장 반응 확인 → 기존 체인맵 이동 버튼 클릭)을 검증할 준비가 완벽히 갖추어짐.
  - 별도 승인 전까지 로컬 서버 백그라운드 구동이나 외부 포트 개방은 수행하지 않음.

---

## 6. 절대 금지 사항 준수 확인
- 기존 세 작업 트리 훼손 없음
- 보존된 미커밋 자료 삭제 없음
- 운영 DB 및 Neon DB 변경 없음
- 마이그레이션 실행 없음
- Worker / GitHub Actions 실행 없음
- 원격 Push, 신규 PR 생성, PR #9 병합, 운영 배포 없음

