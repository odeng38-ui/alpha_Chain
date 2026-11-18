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

## CORE-MARKET-REACTION-01

- 작업: 미국 신호/사건 → 한국 공급망 종목(한미반도체 042700) → 저장 가격·거래량 및 Alpha Score 시장 반응 통합.
- 결과: 한국 장 마감(15:30 KST) 기준 익영업일 첫 반응일 판정, 사건 전후 주가/거래량/변동률 조회, 데이터 결측 시 `insufficient_market_data` 명시 및 비인과 관찰값 고지 구현.
- 검증: `backend/tests/test_core_market_reaction.py` 5건 passed, 기존 15개 격리 테스트 통합 20건 passed (100%), frontend TypeScript noEmit 통과, SSR 3상태 통과.
- 다음 한 가지 작업: 대시보드 UI에 `market_reaction` 반응일·수익률·거래량 변화 표시 카드 레이아웃 검토 및 사용자 승인 대기.

## CORE-DASHBOARD-01

- 작업: Event-First 대시보드(`event-first-dashboard.tsx`)의 한국 공급망 후보 카드에 시장 반응(`market_reaction`) 표시 카드 연결.
- 결과: 8개 항목(기업명/종목/Tier, 공급망 근거, 기준일 및 반응일, 수익률, 거래량 변화율, Alpha Score, 결측/부족 안전 안내, 비인과성 고지) 렌더링 완료.
- 검증: `frontend/tests/core-dashboard-01-render.cjs` 4개 상태(정상 8개 항목, 부족 결측치 미대체, 미제공, ChainMap 연계 버튼 보존) passed, `core-integration-02-render.cjs` 3상태 passed, `npx tsc --noEmit` 통과.
- 다음 한 가지 작업: 브라우저/사용자 상호작용 점검 및 운영 연계 전 데이터 검증 준비.

## CORE-INTEGRATION-06

- 작업: Event-First 전체 파이프라인(미국 신호 → 산업 → 미국 기업 → SEC 사건 → 한국 공급망 → 시장 반응 → 대시보드) 핵심 자산 통합 정리.
- 결과: 총 17개 자산(백엔드 모델·마이그레이션·엔진·신호·앵커·반응 서비스 7개, 프론트엔드 대시보드·타입·스타일 3개, 검증 5개, 문서 2개) 누락 및 충돌 없이 단일 버전으로 통합 완료.
- 검증: `npx tsc --noEmit` 무오류 통과, SSR 렌더링(4상태 + 기존 3상태) 전체 통과, Python 문법 컴파일 전체 통과.
- 다음 한 가지 작업: 실제 브라우저 상호작용 검증 및 로컬 개발 서버 구동 승인 대기.

## CORE-BROWSER-01

- 작업: Event-First 실제 브라우저 통합 검증 시도 및 실행 환경 진단.
- 결과: 샌드박스 환경 내 소켓 바인딩(WinError 10013)으로 실제 브라우저 인터랙션은 '미검증(BLOCKED)'으로 최종 분류. TypeScript 및 SSR 4개 상태(정상/결측/미제공/체인맵버튼) 마크업 무결성은 100% 검증 유지.
- 현재 로컬 상태: HEAD `870499c`, 11개 수정 및 6개 신규 자산 미커밋 보존.
- 다음 한 가지 작업: 샌드박스 외부 로컬 환경에서 사용자가 직접 UI 시각 확인 또는 PR #9 후속 절차 협의.

## CORE-INTEGRATION-07

- 작업: CORE-TIME-02부터 CORE-DASHBOARD-01까지의 핵심 변경사항 18개 파일을 신규 로컬 브랜치 `feat/alpha-chain-core-flow-v1`에 단일 커밋(`ae14898`)으로 확정.
- 결과: 기존 PR #9(`870499c`) 및 다른 세 작업 트리 100% 보존.

## CORE-INTEGRATION-08

- 작업: `feat/alpha-chain-core-flow-v1` 브랜치를 원격에 push하고 PR #9 브랜치를 base로 하는 후속 Draft PR #10 생성 완료.
- 결과: PR #10 생성 완료, CI 실행 중 Backend quality and migration의 Lint 단계 실패 확인.

## CORE-CI-FIX-01

- 작업: PR #10 CI에서 확인된 백엔드 Ruff 16건 오류(미사용 import 8건, 모듈 중간 import 4건, import 정렬 4건) 최소 수정.
- 결과: `ruff check app tests` 0 errors (All checks passed) 달성. Python 컴파일 구문 검증 완료.

## CORE-CI-FIX-02

- 작업: 로컬 검증된 Ruff 수정 커밋 `3da23bd`를 PR #10 원격 브랜치에 반영 및 GitHub Actions CI 결과 읽기 전용 확인.
- 결과: PR #10 HEAD가 `3da23bd`로 갱신됨. Ruff Lint 단계 통과 확인. `Apply migrations on PostgreSQL` 단계에서 Alembic revision ID(40자)가 PostgreSQL `alembic_version.version_num` 컬럼 한도(VARCHAR(32))를 초과한 `StringDataRightTruncation` 오류 1건 원인 규명.

## CORE-CI-FIX-03

- 작업: PR #10의 PostgreSQL CI 마이그레이션 단계에서 발생한 Alembic revision ID 길이 오류(`StringDataRightTruncation`, VARCHAR(32) 초과) 최소 수정.
- 결과: `2026_11_16_core_time_relationship_evidence.py`의 `revision`을 `20261116_core_time_relationship_evidence`(40자)에서 `20261116_core_time_evidence`(27자)로 단축. `down_revision`('20261111_ef14_event_staging') 및 스키마 변경 코드 100% 보존.
- 검증:
  1. Alembic DAG 단일 헤드 `['20261116_core_time_evidence']` 확인.
  2. 전체 22개 마이그레이션 revision 길이 <= 32자 전수 검증 통과.
  3. 격리 SQLite 메모리 환경에서 `upgrade()` -> `downgrade()` -> `re-upgrade()` 실행 통과 (`published_at` nullable 및 `first_seen_at` 추가 정상).
  4. `ruff check backend/alembic/versions` 및 `ruff check app tests` 전체 무오류 통과.
- 다음 한 가지 작업: 로컬 검증된 커밋을 PR #10에 반영하기 위한 사용자 승인 대기.

## CORE-EVIDENCE-02 (Phase 2)

- 작업: SEC EDGAR 공식 공시 수집 어댑터(`SecEdgarAdapter`), 단건 수집 서비스(`SecFilingService`), dry-run 지원 및 격리 SQLite 테스트 구현.
- 결과:
  1. `backend/app/adapters/sec_adapter.py`: SEC 공식 REST Submissions API 클라이언트 구현 (User-Agent 필수 헤더, Rate-limit 10 req/s, 429 지수 백오프, 타임아웃 10s, 404/403 예외 분기, recent filings 정규화).
  2. `backend/app/services/sec_filing_service.py`: 단건 수집 서비스 구현 (페이로드 검증, 미래 시점 차단, dry-run no-write 지원, `SecEventAnchorService.ingest_sec_filing()` 위임, 트랜잭션 commit/rollback 경계 통제).
  3. `backend/app/config.py`: `SEC_USER_AGENT`, `SEC_RATE_LIMIT_PER_SEC`, `SEC_TIMEOUT_SEC` 설정 추가.
  4. `backend/tests/test_sec_ingestion_isolated.py`: 11개 격리 테스트 작성 (Mock 어댑터, Rate-limit, 404, 페이로드 검증, dry-run, persistent proposed, 멱등성, hold 분기, end-to-end CIK 수집).
- 검증:
  - `python -m pytest tests/test_sec_ingestion_isolated.py`: 11 passed (100%).
  - `python -m pytest tests/test_sec_event_anchor.py`: 6 passed (회귀 없음).
  - `python -m ruff check app tests`: All checks passed (0 errors).
- 다음 한 가지 작업: Phase 3 격리 환경 통합 테스트 및 PR 준비 승인 대기.
- 운영 DB 쓰기/마이그레이션/Worker/Actions/배포/Push/PR 없음.

## CORE-EVIDENCE-03: SEC 운영 관리자 API (`POST /api/v1/sec/ingest-one`)

- 작업: 기존 FastAPI 구조에 관리자 전용 SEC 단건 수집 및 dry-run API 구현.
- 결과:
  1. `backend/app/api/v1/endpoints/sec.py`: `POST /api/v1/sec/ingest-one` 엔드포인트 구현 (`require_admin` 인증, `dry_run=True` 기본값, Pydantic 모델).
  2. `backend/app/api/v1/api.py`: `sec_router` 등록.
  3. `backend/app/services/sec_filing_service.py`: `preview_filing()` 추가로 zero-write, zero-flush 순수 읽기 dry-run 구현, 미분류 공시 기본값 `UNCLASSIFIED_FILING` 적용.
  4. `backend/app/adapters/sec_adapter.py`: 가짜/더미 `.local` User-Agent 차단 및 실제 연락처 포함 강제 검증(`validate_sec_user_agent`).
- 검증:
  - 실제 SEC EDGAR 공식 API에서 Micron Form 8-K 조회 성공 (Status 200).
  - Dry-run 실행 시 DB 행 0건(쓰기·flush 일체 없음) 무결성 확인.
  - `dry_run=False` 시 격리 DB에 `GlobalEvent`, `Evidence`, `Anchor`가 정확히 `proposed` 상태로 저장됨 확인.
  - 가짜 User-Agent 요청 시 400 에러 차단 확인.
  - 인증 헤더 부재 시 401 차단 확인.
  - `ruff check app tests`: All checks passed.
- 운영 DB 쓰기/마이그레이션/Worker/Actions/배포/Push/PR 없음.

## AC-KIS-P1: 한국투자증권 KIS Open API 기반 On-Demand 시세 조회 엔진 (Phase 1)

- 작업: KIS Open API를 활용한 특정 한국 상장기업 시세 On-Demand 조회 백엔드 어댑터, 서비스, 관리자 API 구현 및 격리 모의 응답 단위 테스트.
- 결과:
  1. `backend/app/adapters/kis_quote_adapter.py`: KIS OAuth 2.0 Client Credentials 토큰 발급/인메모리 캐시/갱신, 주식현재가 시세(`GET /uapi/domestic-stock/v1/quotations/inquire-price`, TR ID `FHKST01010100`), 기간별 일봉·주봉·월봉(`GET /uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice`, TR ID `FHKST03010100`) 어댑터 구현.
  2. `backend/app/services/kis_quote_service.py`: 6자리 종목코드 및 기간(D/W/M) 검증, 최대 5년 조회 범위 제한, 10초 인메모리 단기 캐시(버스트 중복 방지), 조회 실패/결측 시 `status="no_data"` 통제.
  3. `backend/app/api/v1/endpoints/kis_quotes.py`: 관리자 인증(`require_admin`) 내부 엔드포인트 `GET /api/v1/kis/quotes/{ticker}` 및 `GET /api/v1/kis/quotes/{ticker}/history` 구현.
  4. `backend/app/config.py` & `api.py`: KIS 설정 필드(`KIS_APP_KEY`, `KIS_APP_SECRET`, `KIS_ENV`, `KIS_API_BASE_URL`, `KIS_TIMEOUT_SECONDS`) 및 라우터 등록.
  5. `backend/tests/test_kis_quote_phase1.py`: 필수 9개 검증 항목 단위 테스트 구현.
- 검증:
  - `python -m pytest tests/test_kis_quote_phase1.py -v`: 9 passed in 4.40s (100%).
  - `python -m pytest tests/test_sec_ingestion_isolated.py`: 11 passed (기존 회귀 없음).
  - `python -m ruff check app tests/test_kis_quote_phase1.py`: All checks passed (0 errors).
- 운영 DB 쓰기/마이그레이션/Worker/Actions/실제 KIS 외부호출/배포/Push/PR 없음.
