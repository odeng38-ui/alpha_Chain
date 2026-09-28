# 6단계 관계 추출과 증거 관리

공시 문장을 `POST /api/v1/relationships/extract`에 전달하면 규칙 기반 관계 후보와 근거가 저장됩니다. 외부 LLM 후보는 `POST /api/v1/relationships/candidates`로 제출하며 항상 `proposed` 상태에서 시작합니다.

관리자는 `GET /api/v1/relationships/review-queue`에서 근거를 확인하고 `PATCH /api/v1/relationships/{id}/review`로 승인 또는 거절합니다. 근거가 없는 관계와 하나의 기업으로 확정되지 않는 동명이인 후보는 저장하지 않습니다.

골드셋 평가는 다음과 같이 실행합니다.

```powershell
cd backend
.venv\Scripts\python -m scripts.evaluate_relationships data/predicted.json data/gold/relationships.example.json --output data/gold/evaluation_report.json
```
