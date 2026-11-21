# 데이터 약속 문서 (Data Contract) v1

OLPARK FLOW는 세 부분이 따로 만들어져 하나로 합쳐집니다. 이 문서는 **각자 어떤 파일을, 어떤 모양으로 넘길지** 정한 약속입니다. 이 모양만 지키면 서로 기다리지 않고 동시에 작업할 수 있습니다.

```
 팀원 A (인원 예측·AI)            팀원 B (길 데이터)
 ┌──────────────────┐          ┌──────────────────────┐
 │ events.json      │          │ network.json         │
 │ prediction.json  │          │ routes.json          │
 │ notices.json     │          │ parking.json         │
 └────────┬─────────┘          │ assumptions.json     │
          │                    └──────────┬───────────┘
          ▼                               ▼
        ┌────────────────────────────────────┐
        │ 계산 엔진 src/engine (1분 단위 계산)  │  ← 본인 + Claude
        └─────────────────┬──────────────────┘
                          ▼
                    대시보드 화면
```

- 파일은 모두 `src/data/`에 둡니다. 지금은 목업이고, 진짜 데이터가 나오면 **같은 이름·같은 모양으로 덮어쓰기만** 하면 됩니다.
- 나중에 Python·Spring Boot API로 바꿀 때도 API 응답은 이 문서의 모양 그대로 돌려줍니다 (예: `GET /api/predictions/{eventId}` → 아래 `prediction` 한 건).

---

## 0. 모든 파일에 공통인 규칙

| 항목 | 규칙 | 예 |
|---|---|---|
| 형식 | UTF-8 JSON | |
| 버전 | 파일 맨 위에 `"schemaVersion": 1` | 모양을 바꾸면 숫자를 올리고 팀에 알림 |
| 시간 | 한국 시간 ISO 8601 | `"2026-11-21T21:40:00+09:00"` |
| 단위 | 필드 이름 끝에 단위를 붙임 | `lengthM`(m), `areaM2`(㎡), `minutes`(분), `perMin`(분당) |
| 비율 | 0~1 소수 (퍼센트 아님). 나눠 가지는 비율은 합이 1 | `0.18` = 18% |
| ID | 영문 소문자와 하이픈, 파일 안에서 중복 금지 | `"kspo-dome"`, `"n-junction-center"` |
| 좌표 | WGS84 경위도, **[경도, 위도]** 순서 (지도 서비스와 무관) | `[127.1272, 37.5199]` |
| 이름 | 실제 아티스트명 금지. 공연명은 "공연 A/B/C…" | |
| 출처 | 값마다 어디서 왔는지 `source` 필드로 표시 (아래 표) | |

### 값 출처(`source`) 네 가지
화면의 배지와 1:1로 연결됩니다. 꼭 정확히 적어 주세요.

| 값 | 뜻 | 누가 주로 씀 |
|---|---|---|
| `"예측"` | 모델·계산이 만든 값 | 팀원 A, 엔진 |
| `"입력"` | 운영자가 화면에서 정한 값 | 화면 |
| `"가정"` | 근거는 있지만 실측이 아닌 값 | 팀원 B (길 폭, 처리량 등) |
| `"관측"` | 연동 데이터로 실제 측정한 값 | 팀원 A·B (주차 현황, 승하차 등) |

---

## 1. 팀원 A → `events.json` (공연 목록)

```json
{
  "schemaVersion": 1,
  "events": [
    {
      "id": "kspo-20261121-a",
      "name": "공연 A",
      "venueId": "kspo-dome",
      "venueName": "KSPO DOME",
      "startAt": "2026-11-21T19:00:00+09:00",
      "endAt": "2026-11-21T21:40:00+09:00",
      "seatCapacity": 9500
    }
  ]
}
```

- `venueId`는 다음 넷 중 하나: `kspo-dome`, `ticketlink-live-arena`, `olympic-hall`, `woori-art-hall`
- `endAt`은 **예정 종료 시각**입니다. 계산은 이 시각을 0분으로 놓고 0~60분을 봅니다.

---

## 2. 팀원 A → `prediction.json` (AI 예측 결과)

공연 하나당 한 건. 엔진은 이 값을 **입력값**으로 씁니다.

```json
{
  "schemaVersion": 1,
  "generatedAt": "2026-11-21T12:00:00+09:00",
  "model": { "name": "attendance-gbm", "version": "0.3.0" },
  "predictions": [
    {
      "eventId": "kspo-20261121-a",

      "genre": {
        "value": "아이돌",
        "confidence": "높음",
        "sources": [
          {
            "title": "공연 A 월드투어 서울 공연 안내",
            "url": "https://example.com/notice/1121",
            "quote": "KSPO DOME에서 이틀간 개최되며 팬클럽 선예매를 진행합니다."
          }
        ]
      },

      "attendance": { "value": 7860, "low": 6900, "high": 8700, "interval": "p10-p90" },

      "egressCurve": {
        "totalMinutes": 22,
        "totalMinutesLow": 20,
        "totalMinutesHigh": 24,
        "peakMinute": 6
      },

      "directionShare": { "A": 0.72, "B": 0.19, "C": 0.09 },

      "vehicle": { "ratio": 0.18, "low": 0.14, "high": 0.22 },

      "similarEvents": [
        { "name": "공연 B", "date": "2026-10-18", "genre": "아이돌", "attendance": 8120, "vehicleRatio": 0.17 }
      ]
    }
  ]
}
```

| 필드 | 뜻 | 규칙 |
|---|---|---|
| `genre.value` | 추정 장르 | `아이돌` · `밴드/록` · `발라드/솔로` · `트로트` · `힙합` · `클래식/뮤지컬` · `기타` 중 하나 |
| `genre.confidence` | 확신도 | `높음` · `보통` · `낮음` |
| `genre.sources` | 근거 문장과 링크 | 1건 이상. `quote`는 원문 그대로 |
| `attendance` | 퇴장 인원(관객 수) | `low ≤ value ≤ high`. 엔진은 low·high로 한 번씩 더 계산해 "범위"를 만듦 |
| `egressCurve.totalMinutes` | 관객 대부분(약 95%)이 공연장을 나오는 데 걸리는 시간 | 분 단위 정수 |
| `egressCurve.peakMinute` | 가장 많이 나오는 시각 (종료 후 몇 분) | `0 < peakMinute < totalMinutes` |
| `directionShare` | 지금 운영대로 둘 때 방면별 귀가 비율 | A=올림픽공원역, B=한성백제역, C=몽촌토성역. 합 = 1 |
| `vehicle.ratio` | 차량으로 귀가하는 관객 비율 | `low ≤ ratio ≤ high` |
| `similarEvents` | "근거 보기"에 보여줄 비슷한 과거 공연 | **정확히 5건**, 가까운 순 |

> 예측이 안 되는 값은 지우지 말고 `null`로 두세요. 화면은 `null`이면 "예측 없음"을 띄우고 운영자 입력을 받습니다.

---

## 3. 팀원 A → `notices.json` (공지에서 뽑은 통제 구간)

공지문(LLM 추출 등)에서 찾은 통제 정보입니다. **어느 길인지(`edgeIds`)는 팀원 B의 길 ID를 씁니다.** 길 ID를 모르면 비워 두고 `segmentName`만 적으면 화면에서 운영자가 연결합니다.

```json
{
  "schemaVersion": 1,
  "notices": [
    {
      "id": "notice-20261115-1",
      "segmentName": "평화의문 ↔ 몽촌해자 보행로",
      "edgeIds": ["e-peace-gate-moat"],
      "startAt": "2026-11-18T00:00:00+09:00",
      "endAt": "2026-11-24T23:59:00+09:00",
      "effect": "closed",
      "widthM": null,
      "content": "조형물 설치로 보행로 전면 통제",
      "evidence": {
        "quote": "평화의문 광장 일대 조형물 설치 공사로 해당 기간 보행로 이용이 제한됩니다.",
        "url": "https://example.com/olympicpark/notice/1115",
        "publishedAt": "2026-11-15"
      },
      "confidence": "높음"
    }
  ]
}
```

- `effect`: `"closed"`(아예 막힘) 또는 `"narrowed"`(좁아짐, 이때 `widthM`에 줄어든 폭)
- 기간이 공연 시각과 겹치는 것만 화면에 "적용 가능"으로 뜹니다.

---

## 4. 팀원 B → `network.json` (보행 길 연결)

**어떤 지도 서비스와도 상관없는** 점(장소)과 선(길 토막) 목록입니다. 계산기는 연결·길이·폭만 쓰고, 좌표는 화면에 그릴 때만 씁니다.

- 배경지도를 브이월드·카카오·네이버·OSM 중 무엇으로 바꾸든 이 파일은 그대로 씁니다. 지도에 그릴 때 필요한 형식(GeoJSON 등)은 코드가 이 파일에서 자동으로 만듭니다.
- 좌표는 **WGS84 경위도 `[경도, 위도]`**로 적습니다. 카카오·네이버 지도의 위경도와 같은 체계입니다.
- 공공데이터 중에는 TM 좌표(예: EPSG:5186, 5179)로 된 것이 있습니다. 그런 데이터는 WGS84로 변환해서 넣어 주세요. 원본 좌표계는 `coordSystemOriginal`에 적어 둡니다.

```json
{
  "schemaVersion": 1,
  "coordSystemOriginal": "EPSG:5186",
  "source": "가정",
  "nodes": [ … ],
  "edges": [ … ]
}
```

### 점(`nodes`) = 장소
```json
{
  "id": "n-kspo-exit-east",
  "kind": "exit",
  "name": "KSPO DOME 동측 출구",
  "lngLat": [127.1272, 37.5199],
  "venueId": "kspo-dome",
  "exitShare": 0.4,
  "source": "가정"
}
```

| `kind` | 뜻 | 추가로 필요한 필드 |
|---|---|---|
| `exit` | 공연장 출구 (사람이 나오는 곳) | `venueId`, `exitShare` (같은 공연장 출구끼리 합 = 1) |
| `junction` | 갈림길 (방면이 나뉘는 곳) | |
| `bottleneck` | 좁아서 줄이 생기기 쉬운 곳 | |
| `crossing` | 보행로와 차량 출구 길이 겹치는 곳 | |
| `destination` | 도착점 (지하철역 지상 출입구) | `direction`: `"A"` · `"B"` · `"C"` |

### 선(`edges`) = 길 한 토막
```json
{
  "id": "e-exit-east-to-center",
  "from": "n-kspo-exit-east",
  "to": "n-junction-center",
  "lengthM": 180,
  "widthM": 6.0,
  "type": "flat",
  "queueAreaM2": 900,
  "stepFree": true,
  "path": [[127.1272, 37.5199], [127.1281, 37.5196], [127.1290, 37.5191]],
  "source": "가정"
}
```

| 필드 | 뜻 | 규칙 |
|---|---|---|
| `from` → `to` | 사람이 **나가는 방향** | 둘 다 위 점들의 `id` |
| `path` | 화면에 그릴 길 모양 (선택) | `[경도, 위도]` 목록. 없으면 `from`·`to` 두 점을 곧은 선으로 그림 |
| `lengthM` | 길이 (m) | 걷는 시간 = 길이 ÷ 분당 70m |
| `widthM` | 실제로 걸을 수 있는 폭 (m) | 화단·기둥 등을 뺀 유효 폭 |
| `type` | 길 종류 | `flat`(평지) · `stairs`(계단) · `ramp`(경사로) · `gate`(문) |
| `queueAreaM2` | 이 길 끝에서 줄을 설 수 있는 공간 넓이 (㎡) | 줄이 이 넓이를 넘치면 뒤쪽 길까지 막힘 |
| `stepFree` | 계단 없이 갈 수 있는지 | `true` / `false` |

> 실측이 아니면 `source`를 꼭 `"가정"`으로 두세요. 화면에 "시연용 가정값"으로 표시됩니다.
>
> GeoJSON이나 QGIS로 작업하는 편이 편하면 그렇게 작업해도 됩니다. 넘길 때만 이 모양으로 맞춰 주거나, 원본 파일을 주면 변환 스크립트를 만들겠습니다.

---

## 5. 팀원 B → `routes.json` (방면별 경로)

각 방면(A·B·C)으로 갈 때 **어떤 길 토막을 순서대로 지나는지** 적습니다. 엔진이 길을 스스로 찾지 않고 이 목록을 그대로 따라갑니다.

```json
{
  "schemaVersion": 1,
  "routes": [
    {
      "direction": "A",
      "name": "올림픽공원역 3번 출구",
      "line": "5·9호선",
      "destinationNodeId": "n-dest-olympic-park-3",
      "paths": [
        {
          "exitNodeId": "n-kspo-exit-east",
          "edgeIds": ["e-exit-east-to-center", "e-center-to-a-merge", "e-a-merge-to-station"]
        }
      ]
    }
  ]
}
```

- 출구가 여러 개면 `paths`에 출구마다 한 줄씩 넣습니다.
- 여러 방면이 같은 길 토막을 같이 쓰는 것은 괜찮습니다. 엔진이 합쳐서 계산합니다.

---

## 6. 팀원 B → `parking.json` (주차장과 차량 출구)

```json
{
  "schemaVersion": 1,
  "lots": [
    { "id": "P1", "name": "1주차장", "capacity": 562, "exitIds": ["gate-north-2", "gate-east-2"], "source": "관측" }
  ],
  "exits": [
    {
      "id": "gate-north-2",
      "name": "북2문",
      "throughputPerMin": 4,
      "crossingNodeIds": ["n-crossing-north"],
      "source": "가정"
    }
  ]
}
```

| 필드 | 뜻 |
|---|---|
| `lots[].capacity` | 주차 가능 대수. 현재 값: P1 562 · P2(올림픽홀 지하 포함) 320 · P4 117 · P5 176 · P6 167 · P7 137 |
| `lots[].exitIds` | 이 주차장에서 나갈 수 있는 차량 출구 |
| `exits[].throughputPerMin` | 1분에 빠져나갈 수 있는 차 대수 |
| `exits[].crossingNodeIds` | 이 출구 차들이 지나는 보행·차량 교차점 (network.json의 `crossing` 점) |

> P3가 목록에 없습니다. 없는 게 맞는지 팀원 B가 확인해 주세요.

---

## 7. 팀원 B → `assumptions.json` (계산에 쓰는 가정값)

공식에 들어가는 숫자를 코드가 아닌 파일로 빼 둔 것입니다. **팀원 B가 근거를 찾아 고치면 코드 수정 없이 결과가 바뀝니다.**

```json
{
  "schemaVersion": 1,
  "walkSpeedMPerMin": 70,
  "flowPerMeterPerMin": { "flat": 82, "ramp": 82, "gate": 115, "stairsFactor": 0.6 },
  "weatherFactor": { "맑음": 1.0, "우천": 0.85 },
  "jamAreaPerPersonM2": 0.46,
  "crossingPedestrianShare": 0.6,
  "vehicleOccupancy": 2.4,
  "notes": {
    "flowPerMeterPerMin": "근거 문헌·출처를 여기에 적기",
    "jamAreaPerPersonM2": "1인당 이 넓이에 가까워지면 '위험'으로 표시"
  }
}
```

| 값 | 뜻 |
|---|---|
| `walkSpeedMPerMin` | 걷는 속도 (분당 m) |
| `flowPerMeterPerMin` | 폭 1m당 1분에 지나갈 수 있는 사람 수. 계단은 평지의 `stairsFactor`배 |
| `weatherFactor` | 날씨에 따라 통과량에 곱하는 값 |
| `jamAreaPerPersonM2` | 꽉 막혔을 때 1인당 넓이. 줄 공간 한계 계산과 "위험" 판정에 씀 |
| `crossingPedestrianShare` | 교차점에서 1분 중 보행자가 쓰는 비율 (나머지는 차량) |
| `vehicleOccupancy` | 차 1대당 탑승 인원 (화면에서 바꿀 수 있음) |

---

## 8. 엔진 → 화면 (참고용)

엔진이 돌려주는 결과입니다. 팀원이 만들 필요는 없지만, 결과를 검증할 때 이 이름으로 이야기하면 됩니다.

| 이름 | 뜻 | 단위 |
|---|---|---|
| `maxQueue` | 가장 길었던 줄 | 명 |
| `queueMinutesTotal` | 줄 서서 기다린 시간 합계 | 사람·분 |
| `detourMinutesTotal` | 돌아가느라 더 걸은 시간 합계 | 사람·분 |
| `vehicleQueueMinutes` | 차량 대기 × 탑승 인원 | 사람·분 |
| `clearMinute` | 줄이 다 풀리는 시각 | 종료 후 분 |
| `saturation` | 들어오는 사람 ÷ 지나갈 수 있는 사람 | 비율 (1 넘으면 병목) |

---

## 9. 주고받는 방법

1. 파일은 Git 저장소의 `src/data/`에 올리고, 바꿀 때마다 PR로 올립니다.
2. 모양(필드 이름·종류)을 바꿔야 하면 **먼저 이 문서를 고치고** `schemaVersion`을 올린 뒤 팀에 알립니다.
3. 진짜 데이터가 아직 없으면 목업으로 먼저 개발하고, 나오면 같은 파일을 덮어씁니다.
4. 다음 단계에서 `pnpm validate:data` 검사 명령을 만들 예정입니다. 올리기 전에 돌리면 형식 오류를 바로 알려 줍니다.

## 10. 담당과 확인할 것

| 파일 | 담당 | 먼저 확인할 것 |
|---|---|---|
| `events.json` | 팀원 A | 공연장별 출구 수, 종료 예정 시각을 어디서 가져올지 |
| `prediction.json` | 팀원 A | 퇴장 곡선의 `totalMinutes`·`peakMinute`를 어떤 데이터로 추정할지 |
| `notices.json` | 팀원 A | 공지 수집 경로, 길 ID 연결 방법 (팀원 B와 상의) |
| `network.json` | 팀원 B | 서울시 도보 네트워크에 폭·계단 정보가 있는지, 없으면 현장 확인 범위 |
| `routes.json` | 팀원 B | 방면별 실제 이용 경로 |
| `parking.json` | 팀원 B | P3 여부, 출구별 처리량 근거 |
| `assumptions.json` | 팀원 B | 통과량 82·115명, 0.46㎡의 근거 문헌 |
