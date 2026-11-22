# 원본 데이터

한 번 올리고 고치지 않는 원본입니다. 분석 스크립트는 `geo/validate/`에 있습니다.

| 파일 | 내용 | 출처·이용허락 |
|---|---|---|
| `olympicpark_inout_2025_full.csv.gz` | 2025 올림픽공원 입출차정보(전체구역) 2,411,225건 (2025-01-01 ~ 2026-02-17). 열: `id, entry_gate, entry_time, exit_gate, exit_time` | 한국체육산업개발(주), 문화공공데이터광장 API_CPM_008 (2026-11-22 수집, `geo/validate/fetch_inout_2025.py`). 이용허락범위 제한 없음 |
| `olympicpark_inout_202407.csv.gz` | 2024년 7월 올림픽공원 입출차 정보 215,528건. 열: 입차일, 입차일시, 출차일시 (CP949) | 한국체육산업개발(주), 공공데이터포털 15138735. 이용허락범위 제한 없음 |

- 차량번호 등 개인정보는 없습니다.
- 출구 이름: 북2문·동2문·남2문(체조경기장 주변), 서1문·서2문·남3문·남4문(다른 주차장). `#1`·`#2`는 차로, `CMB`는 원본 표기 그대로입니다.

분석 예: `python3 geo/validate/parking_2025.py geo/data/olympicpark_inout_2025_full.csv.gz`
