# 8단계 규칙 기반 Alpha Score

Alpha Score는 `Macro`, `Industry`, `Fundamental`, `Momentum`, `Disclosure`, `Chain` 여섯 구성요소를 0~100점으로 계산합니다. 총점과 신뢰도는 별도 값입니다.

- 총점: 이용 가능한 구성요소만 대상으로 설정 가중치를 재정규화한 가중평균
- 신뢰도: 입력 완전도 80%와 검증된 관계 근거 품질 20%
- 결측: 50점으로 대체하지 않고 구성요소 값을 `null`로 저장하며 `missing_reason`을 제공합니다.
- 시점 통제: 가격일, 공시시각, 재무 제출시각, 거시지표 `available_at`, 관계 근거 공개시각이 기준일 이하여야 합니다.

## API

- `POST /api/v1/scores/batch`: 점수 배치 계산
- `POST /api/v1/scores/security/{security_id}/calculate`: 단일 점수 계산
- `GET /api/v1/scores/security/{security_id}`: 점수·긍정 요인·위험 요인·입력 추적 조회
- `POST /api/v1/scores/config/weights`: 새 가중치 버전 등록
- `GET /api/v1/scores/config/history`: 가중치 변경 이력 조회

초기 설정은 `app/config/alpha_score_v1.json`에 버전별로 보존됩니다. 관리자 변경은 기존 버전을 덮어쓰지 않고 `alpha_weight_config`에 새 버전으로 기록합니다.
