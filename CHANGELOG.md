# Changelog

## 2026-11-16
- CORE-BROWSER-01: 로컬 실행 환경 점검 완료. 샌드박스 보안 정책(소켓 바인딩 제한)으로 브라우저 상호작용 검증은 '미검증(BLOCKED)'으로 확정하고 SSR 정적 렌더링 무결성 유지.
- CORE-INTEGRATION-06: Event-First 전체 파이프라인(미국 신호/산업/SEC사건 → 한국 공급망 → 시장반응 → 대시보드 카드 12px) 핵심 자산 17개 단일 버전 통합 및 SSR/타입 정적 검증 완료.
- CORE-DASHBOARD-01: `EventFirstDashboard`의 한국 공급망 후보 카드에 사건 전후 시장 반응(`market_reaction`) 카드 및 비인과성 고지 렌더링 연결 완료.
- CORE-MARKET-REACTION-01: `EventFirstQueryService`에 한국 장 마감(15:30 KST) 기준 익영업일 시장 반응(`market_reaction`) 조회 및 결측 안전 처리(`insufficient_market_data`) 추가.

## 2026-11-15
- CORE-INTEGRATION-02: 기존 Event-First GET API·저장 조회 브리지·대시보드를 main 기반 격리 작업 트리에 선별 연결하고, Fixture 및 미검증 사건 공개를 차단했다.
- 새 API 경계 6건·SSR 상태 3건·TypeScript 검사로 변경 범위만 검증했다. 원본 작업 트리와 운영 환경은 보존했다.
- CORE-INTEGRATION-04: 보존 해시와 동일한 연결 코드·검증 파일을 독립 로컬 브랜치에 선별 정리했다. 기능 변경·기존 테스트 반복·원격 작업 없음.
