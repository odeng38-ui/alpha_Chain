# Alpha Chain

## 로컬 실행

필수 도구는 Python 3.11+, Node 20+, Docker입니다.

### Docker 실행

1. `.env.example`을 `.env`로 복사하고 비밀값을 설정합니다.
2. `docker compose up --build`를 실행합니다.
3. API는 `http://localhost:8000/health`, 웹은 `http://localhost:3000`에서 확인합니다.

### 백엔드 직접 실행

```powershell
cd backend
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m alembic upgrade head
.venv\Scripts\python -m uvicorn app.main:app --reload
```

테스트와 lint는 다음과 같이 실행합니다.

```powershell
.venv\Scripts\python -m pytest -q
.venv\Scripts\ruff check app tests scripts
```

개발용 최소 seed는 `python -m scripts.seed`로 적재합니다.

## 2단계: 종목 마스터

`DART_API_KEY`, `KRX_ID`, `KRX_PW`를 설정한 뒤 아래 명령으로 DART와 KRX 식별자를 결합합니다. 현재 pykrx 공급자는 KRX 로그인이 필요합니다.

```powershell
cd backend
.venv\Scripts\python -m scripts.sync_master
```

선택적으로 `ticker,isin,eng_ticker,industry_id` 열을 가진 UTF-8 CSV를
`--supplement-csv`로 전달할 수 있습니다. 결과의 `unmapped` 목록과
`GET /api/v1/master/report`의 매핑률을 반드시 확인합니다.

## 3단계: 최근 5년 일봉

마스터 적재 후 다음 API로 전체 활성 보통주를 최근 5년부터 증분 적재합니다.

```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/v1/prices/incremental -ContentType application/json -Body '{}'
```

실패한 종목은 `collection_checkpoint`에 남고 다음 실행에서 이어서 수집됩니다.
품질 검사는 `GET /api/v1/prices/quality?limit=5000`에서 확인합니다.
최종 승인 검사는 `python -m scripts.verify_stage3`로 실행하며, 매핑률·5년 범위·중복·결측·음수 거래량 중 하나라도 실패하면 종료 코드 1을 반환합니다.

## 환경 분리

## 5단계: FRED 거시경제 데이터

정책금리, CPI, Core PCE, 실업률, 2년·10년물 금리, 장단기금리차, 산업생산, 소매판매, M2를 최초 발표와 수정 빈티지로 수집합니다.

```powershell
cd backend
.venv\Scripts\python -m scripts.sync_fred
```

API는 `POST /api/v1/macro/sync`, `GET /api/v1/macro/series`, `GET /api/v1/macro/regime?as_of=YYYY-MM-DD`입니다. 레짐 데이터셋은 `available_at` 이후의 값만 forward fill합니다.

## 4단계: DART 공시와 재무

최근 1년 공시를 최대 100개 기업에서 증분 수집합니다.

```powershell
cd backend
.venv\Scripts\python -m scripts.sync_dart --limit 100 --days 365
```

연결·별도 재무제표까지 함께 적재하려면 `--financial-year 2025 --report-code 11011`을 추가합니다. 보고서 코드는 1분기 `11013`, 반기 `11012`, 3분기 `11014`, 사업보고서 `11011`입니다.

원문 ZIP까지 보관하려면 `--download-documents`를 추가합니다. API에서는 `POST /api/v1/dart/sync`, 저장 결과는 `GET /api/v1/dart/filings`로 확인합니다.

- 개발: `.env.development`
- 테스트: `.env.test`
- 운영: `APP_ENV=production`; 기본 `SECRET_KEY` 사용이 거부됩니다.

실제 비밀값이 들어간 `.env*` 파일은 Git에서 제외됩니다.
