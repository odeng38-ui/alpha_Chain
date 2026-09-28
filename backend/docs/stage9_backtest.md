# 9단계 백테스트와 평가

백테스트는 저장된 `ScoreSnapshot`을 기준으로 다음 거래 세션에 진입하며, 점수 기준일 이후의 가격만 수익률 계산에 사용합니다.

## 재현 명령

```powershell
cd backend
.venv\Scripts\python -m scripts.run_backtest --name alpha-v1 --score-version v1.0 --horizon 20d --output data/backtest-alpha-v1.json
```

기본 설정은 `app/config/backtest_v1.json`에 보존됩니다. 실행별 설정, 점수 버전, 데이터셋 SHA-256, 파라미터 조정 횟수 및 리포트는 `backtest_run`에 저장됩니다.

## 반영 조건

- 당시 `listed_at`, `delisted_at`, `effective_from`, `effective_to`로 유니버스를 재현합니다.
- 현재 상장폐지된 종목도 당시 유니버스에 속하면 포함합니다.
- 다음 거래일 시가 진입, 지정된 거래일 이후 종가 청산을 사용합니다.
- 거래정지·시세 부재·상한가 진입 불가를 미체결로 기록합니다.
- 매수와 매도 양쪽에 수수료와 슬리피지를 적용합니다.
- 분위별 미래수익률, 승률, 최대낙폭, 회전율과 시장·업종 동일가중 벤치마크 초과수익을 보고합니다.
- 학습·검증·최종 테스트 기간은 시간순으로 분리하며 홀드아웃을 튜닝에 사용하지 않습니다.

`FAILED_ACCEPTANCE`는 실행 오류가 아니라 최소 홀드아웃 표본이나 최대 미체결률 기준을 충족하지 못했다는 뜻입니다. 실패 조건 역시 결과에 보존합니다.
