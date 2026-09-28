# 10단계 웹 MVP

## 화면

1. **오늘의 시장**: 기준일 시점의 거시 레짐, 강한 산업, 신규 공시 이벤트
2. **종목 상세 분석**: 기업 검색, 가격 흐름, Alpha Score와 신뢰도, 재무, 공시 원문
3. **체인맵**: upstream/downstream/both, 1·2·3차 필터, 경로 점수와 관계 근거

모든 화면은 기준일 또는 데이터 갱신시각을 표시합니다. 값이 0이면 숫자 `0`으로, 데이터가 없으면 별도의 빈 상태로 표현합니다. 모바일에서는 그래프 대신 단계별 관계 목록을 먼저 제공합니다.

## 웹 전용 API

- `GET /api/v1/ui/dashboard?as_of=YYYY-MM-DD`
- `GET /api/v1/ui/companies/{company_id}?as_of=YYYY-MM-DD`
- 기존 `GET /api/v1/companies/{company_id}/graph` 사용
- 기존 `GET /api/v1/master/companies` 사용

Next.js는 `/api/backend/[...path]` Route Handler로 FastAPI를 프록시합니다. 배포 환경에서는 `BACKEND_URL`을 설정합니다.

## 접근성 점검표

- 주요 내비게이션에 `nav`와 접근 가능한 이름 적용
- 검색·날짜·방향·홉 컨트롤에 label 또는 `aria-label` 적용
- 키보드 `focus-visible` 표시 적용
- 로딩 상태에 `role=status`, 오류 상태에 `role=alert` 적용
- 가격 SVG에 텍스트 대체 설명 적용
- 근거 원문 링크에 문서 제목을 포함한 접근 가능한 이름 적용
- 색상만으로 값 유무를 구분하지 않고 텍스트 병기
- 모바일 390px 뷰포트에서 단계별 체인 목록 우선 제공

## 대표 사용자 시나리오

1. 오늘의 시장에서 거시 레짐과 신규 이벤트 확인
2. 기업을 검색하고 가격·재무·공시·점수 확인
3. 공시 원문 링크 확인
4. 체인 방향 및 1·2·3차 필터 변경
5. 모바일에서 단계별 체인과 정상 빈 상태 확인
