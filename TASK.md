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

## AC-ADMIN-NBCA-01: Neon Branch Cleanup Advisor (Preview 브랜치 분석 엔진)

- 작업: Neon Preview 브랜치의 불필요한 누적을 방지하고 배포 안정성을 유지하기 위한 읽기 전용 판정 엔진 및 관리자 API 구현.
- 결과:
  1. `backend/app/adapters/neon_branch_reader.py`: Neon API v2 브랜치 및 컴퓨트 엔드포인트 목록 조회 어댑터 (읽기 전용, 토큰 비노출, 수동/오프라인 JSON 분석 모드 지원).
  2. `backend/app/adapters/github_pr_reader.py`: 브랜치명 기반 GitHub PR 상태 대조 (OPEN, MERGED, CLOSED, NOT_FOUND, UNKNOWN).
  3. `backend/app/adapters/vercel_deployment_reader.py`: Vercel Preview 배포 상태 대조 (ACTIVE_PREVIEW, PAST_PREVIEW, FAILED_PREVIEW, UNCONFIRMED_OR_DELETED, NOT_FOUND).
  4. `backend/app/services/neon_branch_cleanup_advisor.py`: 3단계 안전 판정 엔진 (PROTECTED, REVIEW_REQUIRED, DELETE_CANDIDATE). 불확실하거나 데이터 보존 미검증 시 기본 REVIEW_REQUIRED 분류.
  5. `backend/app/api/v1/endpoints/branch_cleanup.py`: 관리자 인증(`require_admin`) 엔드포인트 `GET /api/v1/admin/branch-cleanup/report` 및 `POST /api/v1/admin/branch-cleanup/analyze` 구현 (삭제 실행 기능 배제).
  6. `backend/app/config.py` & `api.py`: Neon/GitHub/Vercel 설정 추가 및 라우터 등록.
  7. `backend/tests/test_neon_branch_cleanup_advisor.py`: 9개 필수 검증 항목 단위 테스트 구현.
- 검증:
  - `python -m pytest tests/test_neon_branch_cleanup_advisor.py -v`: 9 passed in 1.95s (100%).
  - `python -m ruff check app tests/test_neon_branch_cleanup_advisor.py`: All checks passed (0 errors).
- 운영 DB 쓰기/마이그레이션/Worker/Actions/배포/Push/PR/브랜치삭제 없음.

## AC-KIS-P2: KIS 시세 기반 캔들차트 및 이동평균선 구현

- 작업: 기존 EVENT-FIRST 화면에서 한국 상장기업 후보 선택 시 한국투자증권(KIS) 데이터 기반 캔들차트, 이동평균선(SMA), 거래량 히스토그램 및 현재가 시각화.
- 결과:
  1. `frontend/src/lib/kis-types.ts`: StockCandle, KisCurrentQuoteData, KisHistoricalQuotesData, SMAConfig, MovingAveragePeriod 등 정규화된 TypeScript 모델 정의.
  2. `frontend/src/lib/moving-average.ts`: Simple Moving Average(SMA) 모듈 구현 (데이터 부족 구간 null 처리, 0 보간 배제, 시계열 정렬 보존).
  3. `frontend/src/lib/kis-mock-data.ts`: Phase 1.1 실전 검증 결과 기반 한미반도체(042700) 실측치 및 일봉/주봉/월봉 데모 데이터 정의.
  4. `frontend/src/app/api/kis/quotes/[ticker]/route.ts` & `history/route.ts`: 관리자 인증 키 브라우저 노출 방지 서버 Route Handler 구현 (6자리 종목코드/주기 검증, X-Admin-Key 서버 주입, 비인가 요청 401 차단 및 데모 모드 지원).
  5. `frontend/src/components/stock-candlestick-chart.tsx`: 순수 SVG 기반 반응형 캔들차트 구현 (국내 주식 표준 색상 적용, Y축 가격 그리드, 십자선/터치/툴팁 인스펙터, SMA 5/10/20/60/120 폴리라인 오버레이).
  6. `frontend/src/components/stock-volume-chart.tsx`: 캔들과 정렬된 SVG 거래량 히스토그램 구현 (등락별 색상 구분, 십자선 동기화, 거래량 결측 구분).
  7. `frontend/src/components/stock-chart-panel.tsx`: 현재가 스냅샷 헤더(기업명/종목코드/현재가/등락/거래량/기준시각/출처), 일봉·주봉·월봉 전환, 이동평균선 개별 토글 체크박스, 로딩/에러/빈상태 및 유의사항 표시 패널 구현.
  8. `frontend/src/components/event-first-dashboard.tsx`: 한국 기업 후보 카드 내 KIS 시세 차트 열기/닫기 연동, 종목코드 없는 비상장사 안내, 이벤트 변경 시 차트 초기화 구현.
- 검증:
  - `node tests/test-moving-average.cjs`: 7개 테스트 전체 통과 (빈 배열, 주기 초과, 윈도우 슬라이딩, 정렬 등).
  - `node tests/test-chart-panel-render.cjs`: 5개 SSR 렌더링 상태 전체 통과 (캔들/거래량 SVG, 정상 렌더링, 로딩, 빈 데이터, 후보 차트 연동 및 비상장 안내).
  - `node tests/core-integration-02-render.cjs` & `core-dashboard-01-render.cjs`: 기존 테스트 전체 통과 (회귀 없음).
  - `npx tsc --noEmit`: 무오류 통과 (0 errors).
- 보안 및 운영 상태:
  - 브라우저에 KIS App Key/Secret 및 Admin Key 노출 없음 (NEXT_PUBLIC 미사용).
  - 운영 DB 쓰기/마이그레이션/Worker/Cron 실행 없음.
  - Vercel 배포/GitHub Push/PR/main 병합 없음.

## AC-KIS-P2.1: Phase 2 관리자 인증 연결 및 실전 시세 세션 프록시 구축

- 작업: 기존 Phase 2 차트 기능을 유지하면서 관리자 1인 로그인 세션을 통해 실제 KIS 시세를 안전하게 조회할 수 있도록 구현.
- 결과:
  1. `frontend/src/lib/server-auth.ts`: 관리자 비밀번호 검증(SHA-256 해시 대조 및 timingSafeEqual), 서버리스 안전 무상태 HMAC-SHA256 서명 세션 토큰 생성/검증, HttpOnly/SameSite=Lax/Secure 쿠키 설정, Origin CSRF 방어, 인메모리 슬라이딩 윈도우 로그인 시도 제한(15분 내 5회 제한).
  2. `frontend/src/app/api/auth/login/route.ts`, `logout/route.ts`, `session/route.ts`: 관리자 로그인(POST), 로그아웃(POST), 세션 상태 조회(GET) 엔드포인트 구현.
  3. `frontend/src/app/api/kis/quotes/[ticker]/route.ts` & `history/route.ts`: 브라우저의 HttpOnly 관리자 세션 쿠키 검증 연동. 인증 성공 시에만 서버 전용 `ADMIN_API_KEY`를 주입하여 백엔드 KIS API로 대리 호출하고, 미인증 시 401(ADMIN_AUTH_REQUIRED) 반환. 브라우저로 인증 정보 비노출 유지.
  4. `frontend/src/lib/api.ts`: 클라이언트 인증 함수(`checkAdminSession`, `adminLogin`, `adminLogout`) 및 실전 에러 전파 처리.
  5. `frontend/src/components/stock-chart-panel.tsx`: 차트 상단 관리자 인증 상태 표시(`관리자 인증됨` / `관리자 로그인` 버튼), 401 오류 시 인라인 관리자 로그인 폼 제공, 로그인 완료 시 실전 시세 즉시 재조회 및 로그아웃 기능 연동.
- 검증:
  - `node tests/test-admin-auth.cjs`: 비밀번호 해시 검증, 서명 검증, 세션 위조/만료 차단, 속도 제한, CSRF Origin 검사, 미인증 401 차단, 명시적 데모 응답, 세션 기반 실전 KIS 프록시 정상 동작 등 11개 단위/통합 테스트 전체 통과 (100%).
  - `node tests/test-chart-panel-render.cjs`: 6개 SSR 렌더링 상태 전체 통과 (100%).
  - `node tests/test-moving-average.cjs`, `core-integration-02-render.cjs`, `core-dashboard-01-render.cjs`: 전체 통과 (회귀 없음).
  - `npx tsc --noEmit`: 0 errors (TypeScript 컴파일 무오류 통과).
- 보안 및 운영 상태:
  - 브라우저에 KIS App Key/Secret 및 Admin Key 노출 없음 (NEXT_PUBLIC 미사용, HttpOnly 쿠키).
  - 운영 DB 쓰기/마이그레이션/Worker/Cron 실행 없음.
  - Vercel 배포/GitHub Push/PR/main 병합 없음.

## AC-KIS-P2.1-SEC: Phase 2.1 인증 보안 최종 보완 (scrypt·서명키 분리·서버리스 한계 규명)

- 작업:
  1. 비밀번호 검증 강화: Node.js 내장 `crypto.scrypt` (16바이트 솔트 + 64바이트 파생키) 및 `timingSafeEqual` 적용. 단순 SHA-256 및 `ADMIN_API_KEY` 대체 fallback 완전 제거 (Fail-closed).
  2. 비밀번호 해시 안전 생성 스크립트(`frontend/scripts/generate-password-hash.cjs`) 추가: 대화형 마스킹 입력으로 명령줄 인수/셸 기록/프로세스 목록에 비밀번호 비노출.
  3. 세션 서명키 분리: `ADMIN_SESSION_SECRET` 미설정 시 로그인/세션 발급 차단. `ADMIN_API_KEY` 대체 fallback 제거. 세션 토큰 내 민감정보 배제.
  4. 로그아웃 한계 문서화: 무상태 서명 쿠키 특성상 클라이언트 쿠키는 즉시 파기되나, 분산 공유 저장소(Redis 등)가 없는 구조에서 만료 전 토큰의 서버 측 즉시 강제 폐기는 불가능함을 명시.
  5. 서버리스 환경 로그인 시도 제한 한계 규명: 인메모리 Map은 단일 인스턴스/로컬 전용이며 다중 람다 인스턴스 간 공유 불가. 외부 관리형 Rate Limiter(Vercel WAF 또는 Upstash Redis) 부재로 운영 인증 준비 상태를 **BLOCKED**로 판정.
- 검증:
  - `node tests/test-admin-auth.cjs`: scrypt 솔트 검증, 생성 헬퍼, 해시 미설정 fail-closed 차단, 서명키 분리 strict 검증, 위조/만료 차단, CSRF, KIS 프록시 연동 등 11개 단위/통합 테스트 통과 (100%).
  - `npx tsc --noEmit`: 0 errors 통과.
- 운영 환경 미해결 보안 항목:
  - 서버리스 환경 내 분산 공유 Rate Limiting 미연동 (BLOCKED)
  - 서버리스 무상태 토큰 즉시 폐기(Revocation Blacklist) 저장소 부재 (8시간 자연 만료에 의존)

## AC-KIS-P2.2: Phase 2.2 최소 보안 마무리 (세션 단축 및 운영 보안 절차 확정)

- 작업:
  1. 무상태 관리자 세션 유효기간 단축: `frontend/src/lib/server-auth.ts`의 `SESSION_DURATION_SECONDS`를 기존 8시간(28,800초)에서 1시간(3,600초)으로 축소하여 탈취 토큰의 최대 생존 시간을 1/8로 제한.
  2. 세션 만료 테스트 수정 및 검증: `frontend/tests/test-admin-auth.cjs`에서 세션 만료 시간 3,600초 assertion 추가 및 11개 단위/통합 테스트 전체 통과 확인.
  3. Vercel Hobby 환경 WAF Rate Limiting 운영 절차 정리 (설정 변경 없이 문서화).
  4. 프론트엔드 서버 필수 환경변수 4종(`ADMIN_PASSWORD_HASH`, `ADMIN_SESSION_SECRET`, `ADMIN_API_KEY`, `BACKEND_URL`)의 역할 및 사용처 정리 (비밀값 비노출).
- 검증:
  - `node tests/test-admin-auth.cjs`: 1시간 유효기간 검증 포함 11개 테스트 전체 통과 (100%).
- 운영 환경 상태:
  - 운영 DB 쓰기, Neon 브랜치 조작, Worker/Cron 실행 없음.
  - 실제 KIS API 호출 없음 (테스트 모의/격리 유지).
  - Vercel 배포, GitHub Push, PR 생성, main 병합 없음.

## AC-KIS-P2.3: Phase 2.3 자동 DB 마이그레이션 분리

- 작업:
  1. Vercel Preview 및 Production 빌드 시 데이터베이스 마이그레이션 자동 실행 방지: `backend/pyproject.toml`에서 `[tool.vercel.scripts]` build 스크립트(`alembic upgrade head`) 완전 제거.
  2. 기존 Alembic 마이그레이션 파일 및 설정 보존: `backend/alembic/` 디렉터리 및 `backend/alembic.ini`는 변경 없이 유지.
  3. `vercel.json` 멀티서비스 구성 및 `BACKEND_URL` 서비스 바인딩 유지.
  4. DB 마이그레이션 실행 정책 명시: 앞으로 DB 마이그레이션은 빌드/배포 프로세스에서 자동 실행되지 않으며, 사전에 명시적으로 승인된 관리 작업에서만 수동 실행한다.
- 검증:
  - `backend/pyproject.toml`: TOML 문법 파싱 및 유효성 검증 완료 (`[tool.vercel.scripts]` 제거 확인, 프로젝트 의존성 12개 및 ruff 린트 설정 정상 유지).
- 운영 환경 상태:
  - DB 쓰기 및 마이그레이션 실행 없음.
  - Neon 브랜치 생성·삭제 없음.
  - Worker·Cron 실행 없음.
  - 실제 KIS API 호출 없음.
  - GitHub Push·PR 변경, main 병합, Vercel 배포 없음.
