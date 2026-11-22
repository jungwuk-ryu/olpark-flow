# 원본 데이터

한 번 올리고 고치지 않는 원본입니다. 분석 스크립트는 `geo/validate/`에 있습니다.

| 파일 | 내용 | 출처·이용허락 |
|---|---|---|
| `olympicpark_inout_2025_full.csv.gz` | 2025 올림픽공원 입출차정보(전체구역) 2,411,225건 (2025-01-01 ~ 2026-02-17). 열: `id, entry_gate, entry_time, exit_gate, exit_time` | 한국체육산업개발(주), 문화공공데이터광장 API_CPM_008 (2026-11-22 수집, `geo/validate/fetch_inout_2025.py`). 이용허락범위 제한 없음 |
| `olympicpark_inout_202407.csv.gz` | 2024년 7월 올림픽공원 입출차 정보 215,528건. 열: 입차일, 입차일시, 출차일시 (CP949) | 한국체육산업개발(주), 공공데이터포털 15138735. 이용허락범위 제한 없음 |

- 차량번호 등 개인정보는 없습니다.
- 출구 이름: 북2문·동2문·남2문(체조경기장 주변), 서1문·서2문·남3문·남4문(다른 주차장). `#1`·`#2`는 차로, `CMB`는 원본 표기 그대로입니다.

분석 예: `python3 geo/validate/parking_2025.py geo/data/olympicpark_inout_2025_full.csv.gz`

## 2025 연계 데이터 (팀원 A 학습용)

- 파일: `olympicpark_linked_2025.csv.gz`, **7,803건**. 제공: 한국체육산업개발(주), [문화공공데이터광장 API_CPM_009](https://api.kcisa.kr/openapi/API_CPM_009/request), 2026-11-22 수집.
- 차량 비율·퇴장 곡선 학습의 보조 자료다. 실제 응답에는 별도 공연명·공연 종료 시각·관객 수 필드가 없어 공연 날짜 자료와 결합해야 한다. `visitor_count`를 공연 관객 실측으로 간주하지 않는다.
- 열: `id`(LOCAL_ID), `issued_at`(ISSUED_DATE), `parking_count`(주차대수), `visitor_count`(방문객), `temperature`(기온), `wind_direction`(풍향), `precipitation`(강수량), `humidity`(습도), `holiday`(휴일), `collected_at`(COLLECTED_DATE), `registered_at`(REG_DT), `source`(관측).
- DESCRIPTION의 7개 항목을 열로 풀었다. 수치·시각·항목명은 원본 그대로이며, 단위·풍향의 의미는 응답에 없어 추정하지 않았다. TITLE·URL은 제외했다. 실제 아티스트명은 저장하지 않는다.
- 시각 범위: 2025-01-01 00:00:00 ~ 2025-12-31 14:00:00. 고유 ID·시각 각 7,803개. 365일×24시간(8,760개) 대비 **957개 시간 누락**이므로 연속 시계열로 가정하지 않는다.
- 결측은 빈 칸 유지: 기온·풍향·습도 각 1건, 강수량 6,692건, 휴일 7,371건. 강수량 빈 칸을 0으로 바꾸지 않았다.
- 받기: 인증키를 `KCISA_KEY` 환경변수로 설정한 뒤 `python3 geo/validate/fetch_linked_2025.py geo/data`. 16초 간격, HTTP 429·500·502·503·504 재시도. 기존 전체 응답이 있으면 `--xml <응답 파일>`로 재생성한다. 키와 원본 XML은 저장소에 넣지 않는다.
- 이용 조건은 제공기관·문화공공데이터광장 활용신청 화면의 해당 데이터 조건을 확인한다. API 응답에는 별도 이용허락 문구가 없다.
