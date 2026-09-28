# 7단계 1·2·3차 체인 탐색 API

`GET /api/v1/companies/{company_id}/graph`는 근거가 있는 `verified` 관계만 최대 3홉까지 탐색합니다.

## 파라미터

- `direction`: `upstream`, `downstream`, `both`
- `hops`: 1~3
- `relationship_types`: 쉼표로 구분한 관계 유형
- `min_confidence`: 최소 edge 신뢰도
- `as_of`: 기준일 (`YYYY-MM-DD`)
- `limit`, `offset`: 도착 노드 기준 페이지네이션

`downstream`은 관계의 `source_id → target_id`, `upstream`은 반대 탐색 방향입니다. 역방향으로 탐색해도 `graph_edges`의 `source_id`와 `target_id`는 원래 관계 방향을 유지합니다.

경로 점수는 edge confidence 70%, 최신 근거 20%, 근거 수 10%를 결합하고 다중 홉에 감쇠를 적용합니다. 동일 도착 노드로 향하는 경로가 여러 개면 가장 높은 점수의 경로만 반환합니다.

응답은 `graph_nodes`, `graph_edges`, `paths`, `pagination`으로 구성되며 각 edge에 설명과 공개 근거 목록이 포함됩니다.
