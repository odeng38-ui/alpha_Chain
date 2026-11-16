# Current Task

- CORE-INTEGRATION-02: main 146e066 기반 격리 작업 트리의 기존 Event-First 읽기 API·화면 선별 통합.
- 결과: GET 목록/분석 → 저장 ID 브리지 → 기존 EventFirstQueryService/B4/B3 → 저장 가격·점수 → EventFirstDashboard 연결. 기존 ChainMap 진입 연결 유지.
- 검증: 새 HTTP 경계 테스트 6 passed, SSR 3상태 passed, TypeScript noEmit 통과. 브라우저 상호작용·운영 DB 연결은 검증하지 않음.
- 다음 한 가지 작업: 대상 운영 환경의 실제 스키마·Micron 사건/법인/관계·개별 근거 승인 및 시간 계약을 별도 승인 범위로 확정.
- 운영 DB 쓰기/마이그레이션/Worker/Actions/예약/배포/main 병합/Push/PR 없음.

## CORE-INTEGRATION-04

- 작업: 보존된 Event-First 변경을 `feat/core-event-first-integration` 로컬 브랜치에 선별 정리.
- 결과: main `146e066`과 충돌 없음. 기능 코드·검증 파일은 CORE-INTEGRATION-03 manifest와 동일하게 보존하고 실행 산출물·캐시·비밀파일은 Git 대상에서 제외.
- 검증: 기존 HTTP 6건·SSR 3상태·TypeScript·Ruff 결과 재사용. 테스트 재실행 및 브라우저 검증 없음.
- 다음 한 가지 작업: 별도 지시 후 이 로컬 변경 묶음의 리뷰·통합 우선순위 결정. 원격 작업은 미승인.
