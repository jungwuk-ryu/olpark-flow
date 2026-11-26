# 올림픽공원 퇴장 수요 데이터

2025년 올림픽공원 공연 종료 후 교통수요를 위한 자료다. 공연 일정과 상세, 지하철 승차, 버스 이용량, 주차 입출차, 서울 기상 관측, 공원 주변 격자예보, 정류소 위치와 달력을 수집했다. 보행망은 2026년판이다.

원본과 가공 파일은 저장소에 올리지 않는다. 출처마다 이용조건이 다르고 용량이 크기 때문이다. 저장소에는 이 문서와 [수집 명세](manifest.json)만 있고, 아래 표의 파일 링크는 자료를 받은 작업 폴더에서 열린다.

## 예측 서버 실행 자료

새 체크아웃에서 `python -m olpark_demand.server`로 실제 공연을 예측할 때 사용할 파일 목록입니다. 현재 판본은 `rail-2026-11-v3`입니다. 아래 경로는 저장소 루트 기준이며, 파일은 Git에서 제외되어 있습니다. 데이터 담당자에게 같은 판본의 파일을 받아 배치하면 재학습이나 원자료 수집 없이 실행할 수 있습니다.

| 배치할 디렉터리 | 용도 | 받을 파일 |
|---|---|---|
| `ml/data/prepared/` | 공연 일정·발권·휴일 | `event_sessions_2025.csv`, `event_schedule_review.csv`, `olympicpark_events_2025.csv`, `kopis_park_performances_2025.csv`, `calendar_2025.csv` |
| `ml/data/prepared/` | 교통수단별 수요와 목적지 배분 | `rail_station_intervals_2025.parquet`, `parking_gate_hourly_2025.parquet`, `bus_park_stcis_stop_hours_2025.parquet`, `bus_stop_locations.parquet`, `bus_park_monthly_hourly_totals_2025.parquet` |
| `ml/data/prepared/response/` | 과거 회차·시간별 승차·관객 유형 | `sessions.parquet`, `rail_hourly.parquet`, `calendar.csv`, `audience_types.csv` |
| `ml/artifacts/egress-response/rail-2026-11-v3/` | 모델이 읽는 발권·출연자 이력 | `attendance_history.csv`, `act_history.csv` |

마지막 두 파일에는 출연자와 회차별 발권 정보가 있습니다. 권한 있는 팀 경로로 전달하세요. 모델 버전이 바뀌면 새 판본에 대응하는 이력 파일을 사용합니다.

이 16개 파일(현재 합계 약 10.7 MB)과 저장소에 포함된 모델 파일로 공식 달력을 조회하고 예측을 생성할 수 있습니다. ‘신뢰도’의 과거 승차 검증 지표와 공연일 백분위까지 표시하려면 같은 모델 폴더에 `development_forecasts.parquet`를 추가하세요. 서버 실행 명령과 상태 확인 방법은 [루트 README](../../README.md#실제-공연-예측)에 있습니다.

## 바로 사용할 파일

| 파일 | 내용과 단위 |
|---|---|
| [공연 목록](prepared/olympicpark_events_2025.csv) | 공식 달력 12개월에서 행사 ID로 중복을 제거한 125건. 행사기간·장소·공연시간 안내·런타임·등록일·공식 상세 링크 |
| [공연 회차](prepared/event_sessions_2025.csv) | 113개 행사의 예정 시작시각 521건. 날짜가 명시된 회차와 요일 규칙을 적용한 회차를 구분하며 원문을 보존 |
| [회차 확인 목록](prepared/event_schedule_review.csv) | 12개 행사의 원문과 보류 사유. 복수 행사장의 시간, 날짜 충돌, 휴일·주말 규칙 중복, 시작시각 누락 등이 포함됨 |
| [KOPIS 공연 상세](prepared/kopis_park_performances_2025.csv) | 공연 ID·장르·공연장·예정 기간·공연장 좌석 수·관람 가능 연령·출연진·출처. 127건 중 출연진이 기재된 공연은 88건 |
| [T-DATA 정류장·노선별 시간대 승하차](prepared/bus_park_station_hours_2025.parquet) | 공원 주변 261개 정류장, 40일, 979,272행. 승하차 인원과 운행 건수. 날짜별 수집 완결 여부는 `source_day_complete` |
| [서울시 정류장·노선별 일일 승하차 합계](prepared/bus_park_daily_totals_2025.parquet) | 주변 242개 정류장의 365일 자료. 263,563행 |
| [서울시 정류장·노선별 월별 시간대 합계](prepared/bus_park_monthly_hourly_totals_2025.parquet) | 12개월의 월 × 시각별 승하차 합계. 214,152행 |
| [서울 시간별 기상 관측](prepared/asos_seoul_hourly_2025.parquet) | 서울 관측소 108. 기온·강수·습도·풍속·풍향과 품질 플래그. 2025년 8,760시간 |
| [단기예보 전체 발표분](prepared/park_forecast_all_issues_2025.parquet) | 방이2동·오륜동, 변수 6개, 365일 × 하루 8회 발표, 2,505,360행. 지점 파일은 발표 후 6~103시간 범위 |
| [문화데이터광장 출입 차로별 입출차](prepared/parking_culture_api_lane_hourly_2025.parquet) | 7월 31일·8월 2일·12월을 대상으로 공식 API 45/242페이지에서 발췌. 12월 입차 65,534건과 8월 2일 입차 2,073건을 찾았다. 원자료 차로와 시각을 보존했다. [날짜별 범위](raw/D06/culture_api/coverage.json) |
| [출입문별 시간당 차량 입출차](prepared/parking_gate_hourly_2025.parquet) | 1월, 2월, 3월, 4월, 5월, 6월, 7월, 8월, 9월, 10월, 11월 입차와 연결 출차 3,091,265건. 원자료 출입문·시간·방향으로 집계 |
| [17시 발표·20시 대상 격자예보](prepared/park_forecast_1700_for_2000_2025.parquet) | 방이2동·오륜동 기준 격자 2개, 기온·강수량·강수확률·강수형태·풍속·습도. 4,380행 |
| [교통카드 정류장별 시간대 승하차](prepared/bus_park_stcis_stop_hours_2025.parquet) | 국토교통부·한국교통안전공단이 집계한 정류장 전체 노선 승하차. 14개 정류장, 13,776행. [정류장별 수집 일수](raw/D05/stcis/coverage.json) |
| [공원 주변 4개 역·호선 승차 CSV](prepared/park_station_boardings_2025.csv) | 올림픽공원 5·9호선, 몽촌토성 8호선, 한성백제 9호선의 역·날짜·원본 시간구간별 승차 32,120행. 네 역·호선 모두 365일 |
| [지하철 전체 승하차 Parquet](prepared/rail_station_intervals_2025.parquet) | 1~8호선 관할 273개 역번호와 9호선 2·3단계 13개 역번호. 4,212,312개 구간 측정값. 단위는 명 |
| [버스정류소 위치](prepared/bus_stop_locations.parquet) | 서울 전체 10개 기준일, 총 112,995행. 표준 정류소 ID, 5자리 ARS 번호, 위경도, 정류소 유형, 자료 기준일·게시일 |
| [송파·강동 보행망](prepared/walk_network_songpa_gangdong.parquet) | 두 구의 노드·링크 41,053행. 원래 한국어 열 이름, ID, WKT 좌표, 도로 속성을 보존. 서울 전체 파일과 코드표는 raw/D10에 저장 |
| [2025년 달력](prepared/calendar_2025.csv) | 365일. 요일, 토·일, 공휴일 명칭, 전날·다음 날 휴일, 연휴 직전·직후, 임시공휴일 발표일 |

### 행사 반응 모델의 입력

`python -m olpark_demand.response_data`가 `prepared/response/`에 만든다. 2025년 판본의 입력 파일은 바꾸지 않는다.

| 파일 | 내용과 단위 |
|---|---|
| [역·호선 시간별 승하차](prepared/response/rail_hourly.parquet) | 공원 주변 10개 역·호선의 수송일·시각별 승차·하차 인원. 운영기관 파일은 9호선 2023-01-01~2026-07-31, 5·8호선 2025-01-01~2026-06-30. 그 뒤는 대형 회차가 있던 40일(2026-08-01~11-15)을 교통카드 태그 집계에서 채웠다(`source` 열). 자정 이후 구간은 24시로 표시 |
| [공지 회차](prepared/response/sessions.parquet) | 2023~2026년 공원 공지의 모든 회차 3,300여 건. 공연장 구분, 시작 시각, 공지 소요시간, 등록 시각, 관객층·출연자, 2025년 회차의 KOPIS 사후 발권 수 |
| [관객층·출연자 표](prepared/response/audience_types.csv) | 행사 ID별 관객층 7종과 출연자 이름 510건. 공개된 출연자 정보로 직접 분류했다. 2026년 행사는 2026년 승차 자료를 열기 전에 분류했다 |
| [달력](prepared/response/calendar.csv) | 2023~2026년 날짜의 요일 유형과 지정 공휴일 |

| [KOPIS 공연장별 발권 통계 2026](prepared/response/kopis_hall_daily_2026.csv) | 올림픽공원 공연장별 날짜별 상연 횟수와 발권 수. 2026-01-01~11-23 조회, 181일에 공연장 행이 있고 146일은 공원 행이 없음. `olpark_demand.collect_kopis`가 만든다. 원본 XML과 날짜별 응답 상태는 `raw/D02b/` |

### 서울시 교통카드 태그 집계

`python -m olpark_demand.collect_card_tags`가 `prepared/card_tags/`에 만든다. 원본은 [서울시 T-Data 교통카드 데이터](https://t-data.seoul.go.kr/dataprovide/trafficdataviewfile.do?data_id=20006)의 일별 ZIP(하루 290~610MB, 수송일 나흘 뒤 공개)이고, 개인별 태그 기록이므로 집계한 뒤 지운다. 2026-08-01~11-15의 대형 회차가 있던 40일(16.6GB)을 받았다.

| 파일 | 내용 |
|---|---|
| [역 5분 승하차](prepared/card_tags/rail_5min.parquet) | 공원 주변 10개 역·호선, 수송일·방향·5분 구간별 인원. 자정 이후는 1,440분 이상 |
| [역 시간별·권종별 승하차](prepared/card_tags/rail_user_hourly.parquet) | 네 역·호선의 시간·권종(일반·청소년·경로 등 원자료 코드)별 인원 |
| [정류장 5분 승하차](prepared/card_tags/bus_5min.parquet) · [정류장 시간별](prepared/card_tags/bus_hourly.parquet) | 보행망 종점 16개 정류장의 5분 값, 공원 주변 263개 정류장의 시간 값 |
| [같은 날 돌아간 사람](prepared/card_tags/returns.parquet) · [도착](prepared/card_tags/arrivals.parquet) | 공원 역·정류장에 내린 뒤 30분 이상 지나 다시 탄 카드의 승차를 도착 지점·출발 지점·체류 시간대·5분 구간별로 센 값. 카드 번호는 저장하지 않는다 |
| [날짜별 수집 기록](prepared/card_tags/audit.json) · [일별 공표값 대조](prepared/card_tags/daily_check.csv) | 원본 파일명·크기·SHA-256·행 수, 서울시 일별 표와의 차이(승차 최대 0.38%, 하차 최대 0.03%) |

2023~2024년과 2026년 공원 달력의 선택 필드는 `raw/D01b/`에 있다. 2026년 1~8호선 상반기 원본과 9호선 2023~2026년 원본은 `experiments/capacity_study/external_rail/raw/`의 파일을 그대로 읽는다.

## 수집 상태와 출처

| ID | 상태 | 출처 및 범위 |
|---|---|---|
| D01 공연 일정 | 확보 | [한국체육산업개발 공식 달력](https://www.ksponco.or.kr/olympicpark/eventInfo/eventInfoList?mid=a20301010100). 월별 응답에서 공연 관련 공개 필드를 저장. 문화광장 XML 서비스는 점검 중 |
| D02 공연 상세 | 확보 | [KOPIS 공연DB](https://kopis.or.kr/por/db/pblprfr/pblprfr.do?menuId=MNU_00020). 송파구 공식 엑셀 542건에서 올림픽공원 시설 ID로 확인한 공연 127건과 각 상세 화면의 공개 필드를 저장. 2026-11-23 재조회에서 127건 모두 공식 공연 상세 XML을 수신했다. 홀·시각·상품번호 대조와 공연시설별 발권 집계는 [변수 연구](../experiments/variable_audit/event/README.md)에 기록 |
| D03 지하철 1~8호선 | 확보 | [서울교통공사 연간 CSV](https://data.seoul.go.kr/dataList/OA-12921/F/1/datasetView.do). 2025년 365일, 원본 게시일 2026-06-08 |
| D04 지하철 9호선 2·3단계 | 확보 | [서울교통공사 연간 CSV](https://data.seoul.go.kr/dataList/OA-22441/F/1/datasetView.do). 2025년 365일, 원본 게시일 2026-10-02 |
| D05 버스 승객수 | 부분 확보 | [T-DATA](https://t-data.seoul.go.kr/dataprovide/trafficdataviewopenapi.do?data_id=1040). 365일을 각각 조회해 11월 22일~12월 31일의 자료를 받음. 38일 전체 페이지 수집, 12월 29~30일 일부 페이지 서버 오류, 나머지 325일 빈 응답. [서울시 일별 합계](https://data.seoul.go.kr/dataList/OA-12912/A/1/datasetView.do)와 [월별 시간대 합계](https://data.seoul.go.kr/dataList/OA-12913/A/1/datasetView.do)는 12개월 원본을 각각 보관 |
| D06 차량 입출차 | 부분 확보 | [문화 빅데이터 플랫폼](https://www.bigdata-culture.kr/bigdata/user/data_market/detail.do?id=a2d6393d-246e-439b-8bfb-c25204e808fb) 구매 내역에서 입차 기준 1월, 2월, 3월, 4월, 5월, 6월, 7월, 8월, 9월, 10월, 11월 원본을 받았다. 12월 상품은 두 번 모두 2025년 6월·2024년 8월 기록을 반환했다. 7월 31일과 8월 2일 입차 기록도 원본에 없다. [파일별 월과 검산 결과](raw/D06/coverage.json). [전체구역 API](https://www.culture.go.kr/data/openapi/openapiView.do?id=659)에서 2025년 입출차 게시 자료를 조회했다. 원자료의 총 건수·날짜별 수록 범위는 API 수집 내역을 확인한다 |
| D07 ASOS 시간 관측 | 확보 | [기상청 ASOS API](https://www.data.go.kr/data/15057210/openapi.do). 서울 108의 1월 1일 00시~12월 31일 23시 8,760시간. 월별 응답 12개와 품질 플래그 보관 |
| D08 단기예보 | 지점 아카이브 확보 | [기상청 지점 단기예보](https://data.kma.go.kr/data/rmt/rmtList.do?code=420&pgmNo=572) ZIP 두 개, CSV 12개. [발표시각·예보시각 검산](raw/D08/point_archive/coverage.json). 3시간 전 예보 자료는 [기상청 API허브](https://apihub.kma.go.kr/apiList.do?seqApi=10)에서 별도로 확보 |
| D09 정류소 위치 | 확보 | [서울시 버스정류소 위치정보](https://data.seoul.go.kr/dataList/OA-15067/A/1/datasetView.do). 2025년에 게시된 9개 파일과 1월 초 기준으로 쓸 2024-12-09 파일 포함 |
| D10 보행망 | 2026년판 확보 | [서울시 자치구별 도보 네트워크](https://data.seoul.go.kr/dataList/OA-21208/S/1/datasetView.do). 수집일 2026-11-20. 서울 전체 노드 212,066개, 링크 279,016개 |
| D11 공휴일 | 확보 | [한국천문연구원 달력](https://astro.kasi.re.kr/kor/life/post/calendarData?year=2025), [공식 월력요항 발표](https://www.kasi.re.kr/publication/post/newsMaterial/30071?cPage=11), [1월 27일 지정](https://www.korea.kr/news/policyNewsView.do?newsId=148938559), [6월 3일 지정](https://www.korea.kr/news/policyNewsView.do?newsId=148941443). 발표 후 추가된 두 날짜를 포함해 일요일 포함 공휴일 70일 |

## 학습할 때의 기준

- 지하철 승차 학습은 D03의 `flow=승차`, D04의 `flow=순승차`를 사용한다. 키는 자료 ID·수송일자·호선·역번호·승하차구분·시간구간이다. 올림픽공원역 5호선·9호선을 각각 보존했다. D03의 ‘06시이전’과 ‘24시이후’는 원래의 열린 시간구간이다.
- D03에서 완전히 빈 134행을 제거했다. D04는 13개 역 모두 3월 21일과 6월 21일 순하차 기록이 빠졌고, 3월 22일과 6월 22일 순하차는 동일 키에 서로 다른 값이 두 번 들어 있다. 해당 키 624개의 양쪽 값 1,248행을 정제본에서 제외했다. 원본과 전체 순승차 기록을 보존했다.
- 공연 회차는 공지된 예정값이다. 등록시각이 행사 이후인 레코드는 사전 예측에 쓰면 안 된다. 과거 수정 이력은 확보하지 못했다. 실제 종료시각·실관객 수·출구별 비중·차량당 인원·택시와 도보 비중은 운영 자료가 필요하다.
- 버스 위치의 `snapshot_date`는 파일명 기준일, `published_date`는 파일 게시일이다. 분석 기준일까지 공개된 가장 최근 파일을 선택한다. 당일 공개시각은 미상이다. 보행망은 2026년 현장 연결 검토에 쓰며 2025년 역사 재현에 사용하려면 해당 연도 자료를 확보해야 한다.
- 버스 주변 범위는 경도 127.104~127.148, 위도 37.500~37.536으로 추출했다. T-DATA는 표준 정류장 ID로 위치를 연결하고 미정차·빈 노선·노선 0을 제거했다. 같은 이름의 반대편 정류장은 ID로 구별한다. 원자료의 빈 승차·하차·운행 인원은 NULL로 보존했다. [날짜별 API 응답 및 누락 페이지](raw/D05/download_counts.json)
- 서울시의 일별 합계는 노선번호 단위이고, 시간대 합계는 월 단위다. T-DATA의 노선 ID와 서울시의 노선번호는 별도 열이다. 세 자료를 합산하면 이용량이 중복된다. 서울시 월별 자료의 위치 필터에는 해당 월 1일 현재 알려진 정류소 정보를 적용했다.
- 교통카드빅데이터 통합정보시스템에서 공원·인접 역명으로 추린 16개 후보 중 14개가 포털의 ARS 목록과 일치했다. 14곳에서 승하차 통계를 받았다. 시간 열은 04시~23시, 00시~03시 순으로 제공됐다. [정류장별 관측 일수](raw/D05/stcis/coverage.json)
- 문화데이터광장 API에서 확보한 12월 입차 시각은 2025-12-01 00:06:17~2025-12-07 16:08:25다. 7월 31일의 입차는 수신한 페이지에 0건이었다. API와 월별 파일의 동일 시각·출입문 대조에서 2월 이후 기록 대부분이 일치하지 않아 출처별 집계를 사용한다. [대조 결과](raw/D06/culture_api/coverage.json)
- 주차 CSV 합본은 표제와 실제 입차 월이 다를 수 있다. 12월에 관측된 출차 480건은 11월까지 입차한 차량에서 나온 값이다. 12월 출차 전체를 분석하려면 12월 입차 원본을 확보해야 한다. 원자료 출입문이 사전으로 표시된 출차는 위치가 확인되지 않은 범주다. 집계에 없는 시간과 출입문은 원자료 수집 범위를 확인한 뒤 사용한다.
- 기상 자료는 ASOS 실측, 단기예보 지점 아카이브와 격자 API의 발표시각을 구분해 사용한다. 지점 자료의 확대기간 풍속·강수량은 value_encoding 열에 범주 코드로 표시했다. 기온 결측 코드는 원본값을 보존하고 값 열에는 NULL을 기록했다.
- 공휴일 API의 지정 공휴일 19개 날짜는 달력과 전부 일치했다. API의 20개 항목 중 5월 5일에는 어린이날·부처님오신날이 겹친다.
- 달력의 `is_public_holiday`는 일요일과 지정 공휴일을 포함한다. `*_day_off`는 주 5일 근무 기준으로 토요일도 포함한다. `before_break`·`after_break`는 이틀 이상 연속으로 쉬는 기간과 맞닿은 근무일이다. 1월 27일과 6월 3일의 발표 전 시점으로 되돌려 분석할 때는 당일·인접일 특성 모두에서 해당 지정 효력을 제외한다.
- 연간 지하철 파일의 게시일은 관측연도 이후다. 이 파일로 과거 정답을 만들 수 있으며, 당일에 조회할 수 있었다는 증거로 사용할 수는 없다.

출처별 상태, 원본의 게시일·이용조건, 파일별 SHA-256과 행 수는 [수집 명세](manifest.json)에 있다. 인증키와 개인정보는 데이터 폴더 밖에서 관리한다.

## 인증키

**예측 서버 실행에는 API 키가 필요 없습니다.** 서버는 올림픽공원 공개 달력에서 공연 일정을 받고 로컬 모델과 이력 파일로 예측합니다. 교통카드 태그 수집기와 공원 달력 수집기도 키 없이 동작합니다.

**KOPIS 발권 통계를 새로 받을 때**는 공연예술통합전산망에서 Open API 서비스키를 발급받아 `KOPIS_API_KEY`로 설정하세요. Python 수집기는 실행한 프로세스의 환경변수를 읽습니다. 프런트엔드 프로젝트의 `.env.local`은 수집기에 로드되지 않습니다. [KOPIS 공식 사이트](https://kopis.or.kr/) · [KOPIS Open API 개발 가이드](https://kopis.or.kr/upload/openApi/공연예술통합전산망OpenAPI개발가이드.pdf)

다음 예시는 `ml/`에서 프로젝트의 Python 가상환경을 활성화한 상태를 기준으로 합니다. 현재 터미널에서 키를 입력하면 같은 터미널에서 시작하는 수집기에 전달됩니다.

**Windows PowerShell**

```powershell
$env:KOPIS_API_KEY = [System.Net.NetworkCredential]::new('', (Read-Host 'KOPIS API 키' -AsSecureString)).Password
```

**Linux·macOS (Bash 또는 zsh)**

```sh
printf 'KOPIS API 키 입력 후 Enter: '
IFS= read -rs KOPIS_API_KEY
printf '\n'
export KOPIS_API_KEY
```

두 환경 모두 아래 명령으로 키의 등록 여부를 확인할 수 있습니다. 값 자체는 출력하지 않습니다. 수집 예시는 2026년 11월 1일 하루 분량입니다.

```sh
python -c "import os; print('설정됨' if os.getenv('KOPIS_API_KEY') else '미설정')"
python -m olpark_demand.collect_kopis --start 2026-11-01 --end 2026-11-01
```

macOS에서는 환경변수가 비어 있으면 수집기가 로그인 키체인의 `kr.olpark-flow.KOPIS_API_KEY` 항목도 조회합니다. Windows와 Linux에서는 위 환경변수를 사용하세요.
