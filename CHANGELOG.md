# Changelog

## 2026-11-18
- AC-KIS-P1: 한국투자증권 KIS Open API 기반 On-Demand 시세 조회 엔진 구축 (현재가 FHKST01010100, 일/주/월봉 FHKST03010100, OAuth 토큰 관리, 인메모리 단기 캐시, 관리자 인증 API, 9개 격리 단위 테스트 통과).

## 2026-11-17
- CORE-EVIDENCE-03: 관리자 전용 SEC 단건 수집 API(`POST /api/v1/sec/ingest-one`), zero-write dry-run 및 실제 SEC EDGAR 조회 연동 완료.
- CORE-EVIDENCE-02 (Phase 2): SEC EDGAR 공식 공시 어댑터(`SecEdgarAdapter`), 단건 수집 서비스(`SecFilingService`), dry-run 지원 및 11개 격리 테스트 구현 완료.

## 2026-11-16
- CORE-CI-FIX-03: Alembic revision ID를 `20261116_core_time_evidence`(27자)로 최소 수정하여 PostgreSQL `alembic_version` VARCHAR(32) 한도 준수 및 격리 마이그레이션 검증 완료.
- CORE-CI-FIX-02: PR #10에 Ruff 수정 커밋 `3da23bd` 반영 완료. Lint 통과 확인 및 PostgreSQL 마이그레이션 단계의 Alembic revision 문자열 길이 초과(32자 한도) 오류 규명.
- CORE-CI-FIX-01: PR #10 백엔드 CI 차단 원인이었던 Ruff 16건 오류(미사용 import 8건, 중간 import 4건, 정렬 4건) 최소 수정 완료 및 `ruff check` 전체 통과.
- CORE-INTEGRATION-08: `feat/alpha-chain-core-flow-v1` 원격 push 및 PR #9 base 후속 Draft PR #10 생성 완료.
- CORE-INTEGRATION-07: CORE-TIME-02부터 CORE-DASHBOARD-01까지 핵심 변경사항 18개 파일 신규 로컬 브랜치 `feat/alpha-chain-core-flow-v1` 커밋(`ae14898`) 확정.
- CORE-BROWSER-01: 로컬 실행 환경 점검 완료. 샌드박스 보안 정책(소켓 바인딩 제한)으로 브라우저 상호작용 검증은 '미검증(BLOCKED)'으로 확정하고 SSR 정적 렌더링 무결성 유지.
- CORE-INTEGRATION-06: Event-First 전체 파이프라인(미국 신호/산업/SEC사건 → 한국 공급망 → 시장반응 → 대시보드 카드 12px) 핵심 자산 17개 단일 버전 통합 및 SSR/타입 정적 검증 완료.
- CORE-DASHBOARD-01: `EventFirstDashboard`의 한국 공급망 후보 카드에 사건 전후 시장 반응(`market_reaction`) 카드 및 비인과성 고지 렌더링 연결 완료.
- CORE-MARKET-REACTION-01: `EventFirstQueryService`에 한국 장 마감(15:30 KST) 기준 익영업일 시장 반응(`market_reaction`) 조회 및 결측 안전 처리(`insufficient_market_data`) 추가.

## 2026-11-15
- CORE-INTEGRATION-02: 기존 Event-First GET API·저장 조회 브리지·대시보드를 main 기반 격리 작업 트리에 선별 연결하고, Fixture 및 미검증 사건 공개를 차단했다.
- 새 API 경계 6건·SSR 상태 3건·TypeScript 검사로 변경 범위만 검증했다. 원본 작업 트리와 운영 환경은 보존했다.
- CORE-INTEGRATION-04: 보존 해시와 동일한 연결 코드·검증 파일을 독립 로컬 브랜치에 선별 정리했다. 기능 변경·기존 테스트 반복·원격 작업 없음.
