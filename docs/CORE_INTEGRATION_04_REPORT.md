# CORE-INTEGRATION-04 — 로컬 브랜치 정리

## 기준과 범위

- 브랜치: `feat/core-event-first-integration`.
- 기준 main: `146e066b5f58179b736a1f09c10ae190a2263985`.
- GitHub main과 로컬 origin/main 일치 확인. 기존 격리 worktree만 사용.
- CORE-INTEGRATION-03의 manifest 14개 및 patch 해시 일치 확인 후 정리.
- 실제 공통 파일 충돌 없음. 기능 소스·검증 파일 10개는 보존 manifest 그대로 유지.

## main 대비 포함 대상

수정 파일 5개:

- backend/app/api/v1/api.py
- frontend/src/app/page.tsx
- frontend/src/app/globals.css
- frontend/src/lib/api.ts
- frontend/src/lib/types.ts

신규 기능 연결 파일 3개:

- backend/app/api/v1/endpoints/event_first.py
- backend/app/services/event_first_stored_data_bridge.py
- frontend/src/components/event-first-dashboard.tsx

기존 격리 검증 파일 2개:

- backend/tests/test_core_integration_02.py
- frontend/tests/core-integration-02-render.cjs

작업 문서 5개:

- TASK.md
- PROJECT_STATUS.md
- CHANGELOG.md
- docs/CORE_INTEGRATION_02_REPORT.md
- docs/CORE_INTEGRATION_04_REPORT.md

## 제외 대상

- backend/.core-integration-02-results 아래 JSON·HTML 실행 산출물.
- node_modules junction, 캐시, 가상환경, 환경변수·비밀파일.
- 형제 preservation, core-integration-03, ZIP·patch·해시 보존 자료.
- 기존 main의 B3/B4/QueryService/ChainMap, 모델 및 기본 migration.
- 추가 승인·스냅샷, DATA05 별도 검증, Worker/Actions/예약 관련 코드.

## 의존성·충돌 및 기존 검증

- 기존 main의 B3/B4/QueryService, UI 상태 컴포넌트, Next 프록시를 재사용한다.
- 이 연결 묶음에 필요한 누락 코드 의존성은 확인되지 않았다.
- 추가 승인·스냅샷 코드는 필수 import가 아니며 포함하지 않는다.
- 새 패키지·설정·마이그레이션 변경 없음.
- 기존 HTTP 6건, SSR 3상태, TypeScript, Ruff 결과를 재사용하며 재실행하지 않았다.
- 소스·검증 파일이 보존본과 동일하므로 이번 브랜치 정리로 인한 코드 재검증은 하지 않았다.
- 실제 브라우저 클릭·프록시 왕복은 여전히 미검증이다.
- 운영 B4 스키마·실제 사건/법인/관계·개별 근거 승인·공개시각 공백은 별도 작업이다.

## 후속 PR 범위

별도 승인으로 PR을 만들 경우 위 15개 파일만 대상으로 한다.
기준 main이 바뀌면 공통 파일 5개를 재대조해야 한다.
로컬 커밋은 운영 반영·배포·main 병합을 뜻하지 않는다.
이번 작업에서는 Push·PR·병합·배포·DB·Worker·Actions·예약 실행을 하지 않는다.
