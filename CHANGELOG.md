# Changelog

## 2026-11-20
- AC-KIS-P3.2: Phase 3.2 사용자 조회 권한 분리 (VIEWER와 ADMIN 역할 분리, Viewer 전용 scrypt 비밀번호 해시 및 서명 키 분리, 7일 유효 HttpOnly·SameSite=Lax 쿠키 발급, KIS 현재가/과거 캔들 GET 전용 권한 부여, 백엔드 관리자 프록시 및 데이터 변경 요청 차단, StockChartPanel 일반 조회 및 관리자 탭 분리, 로그아웃 시 실측 캐시 즉시 소거, Preview 전용 활성화 게이팅, 단위/통합 테스트 통과).

## 2026-11-18
- AC-KIS-P3.1: Phase 3.1 종목 분석 KIS 온디맨드 연동 (종목 상세 분석 화면에 StockChartPanel 온디맨드 통합, 최초 진입 시 KIS 호출 0회 보장, 클라이언트 2단계 캐시 [현재가 60초, 과거 캔들 10분] 적용, 일/주/월봉 독립 캐시 및 빠른 종목 전환 시 잔존 데이터 누수 방지, 격리 테스트 7건 통과).
- AC-KIS-P2.8: Phase 2.8 관리자 변경 API 보안 강화 (인증 방식과 출처 검증 엄격 분리, 세션 쿠키 기반 변경 요청 시 Origin/Referer 누락 403 차단, 외부 출처 및 Origin/Referer 불일치 차단, 출처 없는 순수 서버 간 API 키 허용 및 혼재 시 우회 방지, 8개 단위/통합 테스트 통과).
- AC-KIS-P2.7: Phase 2.7 운영 반영 전 보안·표시 보완 (관리자 데이터 변경 API CSRF Origin 검증 추가, KIS 시세 5단계 상태 표시 구분, 서버 간 ADMIN_API_KEY 보호 확인, 격리 테스트 통과).
- AC-KIS-P2.5: Phase 2.5 관리자 인증 보안 수정 (`/admin/login` 로그인 화면 구현, `/admin` 및 `/admin/ingestion` 서버 측 세션 검증 및 미인증 리다이렉트 적용, `/api/backend/[...path]` 관리자 경로 및 데이터 변경 요청 401 차단, 서버 전용 `ADMIN_API_KEY` 주입 및 클라이언트 비노출, 단위/통합 테스트 통과).
- AC-KIS-P2.3: Phase 2.3 자동 DB 마이그레이션 분리 (`backend/pyproject.toml`에서 Vercel 빌드 시 자동 alembic upgrade 실행 스크립트 제거, DB 마이그레이션을 사전 승인된 수동 관리 작업으로 분리).
- AC-KIS-P2.2: Phase 2.2 최소 보안 마무리 (무상태 관리자 세션 만료 시간을 8시간에서 1시간/3,600초로 단축, 만료 범위 검증 테스트 수정 및 11개 단위/통합 테스트 통과, Vercel Hobby 환경 WAF Rate Limiting 절차 및 프론트엔드 필수 환경변수 4종 역할 정리).
- AC-KIS-P2.1-SEC: Phase 2.1 인증 보안 최종 보완 (`crypto.scrypt` 솔트 해시 적용, `ADMIN_SESSION_SECRET` 서명키 엄격 분리 및 fallback 제거, 마스킹 입력 기반 해시 생성기 추가, 무상태 로그아웃 한계 및 서버리스 분산 Rate Limiting 부재로 인한 운영 인증 준비 BLOCKED 상태 명시).
- AC-KIS-P2.1: Phase 2 관리자 인증 연결 (관리자 1인 로그인, SHA-256 비밀번호 검증, HttpOnly/SameSite=Lax HMAC-SHA256 서명 세션, 세션 위조/만료 방어, CSRF Origin 검사, 로그인 시도 제한, KIS 프록시 세션 연동 및 11개 단위/통합 테스트 통과).
- AC-KIS-P2: 한국투자증권(KIS) 시세 기반 캔들차트, 이동평균선(SMA 5/10/20/60/120), 거래량 히스토그램 및 EVENT-FIRST 대시보드 연동 구현.
  - SVG 기반 고성능 반응형 캔들차트 및 거래량 차트 컴포넌트(`StockCandlestickChart`, `StockVolumeChart`, `StockChartPanel`) 구현.
  - 일봉(D)·주봉(W)·월봉(M) 주기 전환, SMA 5·10·20·60·120 개별 토글 및 십자선/툴팁/터치 인스펙터 지원.
  - 이동평균선(SMA) 독립 계산 모듈(`moving-average.ts`) 및 단위 테스트 7건 통과 (데이터 부족 시 null 처리, 가짜 보간 없음).
  - 관리자 인증 안전 서버 Route Handler(`app/api/kis/...`) 및 한미반도체(042700) 검증 실전 데이터 기반 모의/데모 폴백 제공.
  - EVENT-FIRST 후보 종목 선택 연동 및 비상장 종목 시세 불가 안내 처리. SSR 렌더링 테스트 및 TypeScript noEmit 무오류 통과.
- AC-ADMIN-NBCA-01: Neon Branch Cleanup Advisor 구현 (Neon 브랜치·GitHub PR·Vercel 배포 3자 대조, PROTECTED/REVIEW_REQUIRED/DELETE_CANDIDATE 3단계 안전 판정, 관리자 전용 분석 API 및 9개 격리 테스트 통과).
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
