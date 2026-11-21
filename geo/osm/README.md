# 오픈스트리트맵으로 길 뼈대 만들기

`src/data/network.json`, `routes.json`의 현재 뼈대는 이 스크립트로 만들었습니다.
데이터 © OpenStreetMap contributors, ODbL 1.0 (https://www.openstreetmap.org/copyright). 화면 하단 출처에 표기합니다.

## 다시 만드는 순서

```bash
# 1. 공원 일대 보행로·지하철 출입구·공연장 건물 내려받기 (Overpass API, 계정 불필요)
curl -s --data-urlencode "data@geo/osm/overpass-query.txt" https://overpass-api.de/api/interpreter -o geo/osm/osm.json

# 2. 출구 → 역 3곳 최단 경로를 찾고 토막을 합쳐 뼈대 만들기 (결과: 지정 폴더에 network.json, routes.json)
mkdir -p geo/osm/out
node geo/osm/build-skeleton.mjs geo/osm/osm.json geo/osm/out

# 3. 확인용 그림 (브라우저로 열기)
node geo/osm/render-preview.mjs geo/osm/out/network.json geo/osm/out/routes.json geo/osm/osm.json geo/osm/out/preview.html

# 4. src/data에 반영: 교차점 후보 지정, 공지 길 ID 재연결, 역 출입구 계단 토막 추가
cp geo/osm/out/network.json geo/osm/out/routes.json src/data/
node geo/osm/apply-skeleton.mjs geo/osm/out src/data
node geo/osm/add-station-stairs.mjs src/data

# 5. 폭 측정 기록표 갱신, 검사
node geo/make-measurements.mjs
pnpm validate:data
```

`osm.json`과 `out/`은 다시 받을 수 있으므로 커밋하지 않습니다.

## 스크립트가 정한 것 (모두 "가정", 팀원 B가 확인)

| 항목 | 정한 방법 |
|---|---|
| 공연장 출구 | 올림픽체조경기장 건물 둘레에서 남·동·서쪽으로 가장 가까운 보행로 점. 실제 출구 위치·개수와 다를 수 있음 |
| 출구 비율 | 3곳 똑같이 1/3 |
| 경로 | 거리 기준 최단 경로 (계단은 1.2배로 쳐서 조금 피함). 실제로 많이 걷는 길과 다를 수 있음 |
| 길 폭 | OSM `width` 태그가 없으면 종류별 기본값 (보행로 3m, 공원 도로 4m 등) |
| 줄 설 공간 | 폭 × 길이(10~40m) |
| 역 출입구 | 역마다 20m · 폭 3m 계단 토막을 끝에 붙임 |
| 교차점 | A 방면 경로에서 OSM "공원 주차장"에 가장 가까운 점을 후보로 지정 |
