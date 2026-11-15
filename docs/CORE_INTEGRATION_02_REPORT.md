# CORE-INTEGRATION-02 결과 — 2026-11-15

## 범위와 기준

- 격리 detached worktree, 기준 main `146e066b5f58179b736a1f09c10ae190a2263985`.
- source: `C:/tmp/alphachain-phase-d2`, HEAD `e30de5b6fcca65bbcb2c13ea3288ce7788a386a6`의 기존 스테이징 구현에서 필요한 연결만 선별.
- 기존 작업 트리 수정, 운영 DB 접속·쓰기, migration, Worker, Actions, 예약, 배포, commit/push/PR/merge 없음.

## 원본 보존

형제 `preservation` 폴더에 staged.patch, unstaged.patch, index.snapshot,
status.z, HEAD, 미커밋·미추적 원본 169개와 SHA-256 manifest를 저장했다.
작업 종료 시 원본 169개 해시, source Git status, source index bytes 모두 동일했다.
격리 worktree 생성에 필요한 Git worktree 메타데이터만 등록했으며 기존 브랜치는 이동하지 않았다.

## 통합 파일

| 파일 | 출처 및 필요한 최소 수정 |
|---|---|
| backend/app/api/v1/endpoints/event_first.py | 기존 GET 목록·분석 경계 선별. POST/가격감시/Worker 운영표시/파급효과 별도 API 제외. Fixture 차단, pending_data 및 503 저장소 오류 구분 |
| backend/app/services/event_first_stored_data_bridge.py | 기존 브리지의 저장 ID 해석을 선별. 별도 후보 탐색 대신 main B4로 위임. Demo fallback·승인/스냅샷 의존성 없음 |
| backend/app/api/v1/api.py | 기존 라우터 import/include 2줄 |
| frontend/src/components/event-first-dashboard.tsx | 기존 staged UI 재사용. Demo 토글·일반 목록 fallback·Micron 고정 설명·추정 방향/기본 1홉 제거. 기존 ChainMap 진입 callback·문서 위치 표시 |
| frontend/src/app/page.tsx | 기존 이벤트 탭·화면 연결, 기존 ChainMap으로 기업 선택 전달 |
| frontend/src/lib/api.ts | 기존 GET 클라이언트·UTC 종료시각 계약 |
| frontend/src/lib/types.ts | 기존 Event-First 응답 타입 |
| frontend/src/app/globals.css | 기존 staged 이벤트 화면 스타일 15줄 |

검증 파일: `backend/tests/test_core_integration_02.py`,
`frontend/tests/core-integration-02-render.cjs`.
작업 기록: TASK.md, PROJECT_STATUS.md, CHANGELOG.md.

## 실제 코드 경로

기존 page 이벤트 탭 → EventFirstDashboard → GET client → 기존 Next 프록시
→ FastAPI Event-First router → EventFirstStoredDataBridge
→ main EventFirstQueryService → main B4 EventSupplyChainService → main B3.
저장 가격·Alpha Score는 기존 QueryService가 조회하며 새 계산·배치는 하지 않는다.
후보의 기존 체인맵 열기는 기존 page의 기업 선택과 ChainMap 화면으로 연결한다.
경제적 파급 설명을 관계 홉으로 변환하거나 새 경로 점수를 만들지 않았다.

기존 d2 브리지의 직접 저장 조회는 B4의 사건·앵커 검증 조건을 그대로 거치지 않았다.
이를 통합하는 대신 main B4로 위임하는 최소 대체를 사용했다.
추가 승인·스냅샷 코드가 없어서 생기는 import 충돌은 없다.

## 변경 경계 검증

- 새 HTTP 경계 테스트만 6 passed: 빈 DB, demo 차단, 미존재 Fixture ID 404,
  미승인 사건 보류, 외부 ID의 B4 위임·기준시각 전달,
  미승인 사건 근거 배제, timezone 검증 및 스키마 부재 503 포함.
- 인메모리 SQLite만 사용. 빈 목록·미승인 사건 HTTP 조회에는 SQL SELECT-only guard 적용.
- SSR 3상태 통과: 빈 목록, 위 HTTP 테스트의 실제 pending 응답, 저장소 오류.
  React 효과는 끈 상태로 렌더링했으며 브라우저 클릭/네트워크 상호작용 검증은 아니다.
- TypeScript `--noEmit --incremental false` 통과.
- 수정 Python 파일 Ruff 및 git diff --check 통과.
- 기존 B3/B4/승인·철회/시점 차단 전체 테스트는 재실행하지 않았다.
- 기존 FastAPI TestClient 및 datetime.utcnow 관련 deprecation 경고 9개. 의존성 변경 없음.
- 패키지 설치 없음. 격리 frontend/node_modules는 기존 d2 의존성을 가리키는 junction이며
  TypeScript 캐시를 쓰지 않았다.

렌더 산출물과 격리 HTTP 응답은 backend/.core-integration-02-results에 있다.
React 체크: 목록·상세 요청의 늦은 응답 무시, 오류/빈 상태 분리,
미작동 심층 분석 비활성 유지, 기존 타입 검사 및 디자인 유지.

## 운영 조건과 다음 병목

이 결과는 격리 코드 통합이며 운영 사례 성공·main 반영·배포를 뜻하지 않는다.
로컬 운영 DB의 B4 필수 스키마·Micron 사건/법인/관계·개별 근거 승인 공백,
관계 원문 공개시각 미상 문제는 해결하지 않았다. Neon 상태 UNKNOWN 유지.
데이터가 없으면 빈 목록 또는 후보 보류, 스키마가 맞지 않으면 503으로 반환한다.
Fixture를 운영 사건으로 대체하지 않는다.

다음 단일 병목: 별도 승인 아래 대상 운영 DB의 B4 최소 데이터 계약
(스키마 이력·실제 ID·승인·근거 이용 가능시점)을 충족시키는 범위 확정.

## 보존한 main 자산

B3, B4, EventFirstQueryService, ChainMap, DB 모델·Alembic 폴더는
main 대비 diff가 없음을 종료 시 확인했다.
