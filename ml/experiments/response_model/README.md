# 행사 반응 모델: 고정한 시험 밖에서 한 분석

고정한 규칙과 2026년 채점은 [판본](../../artifacts/egress-response/rail-2026-11/MODEL_CARD.md)에 있다. 이 폴더의 분석은 모두 그 채점을 본 **뒤에** 했다. 모델을 바꾸지 않고, 채점에서 드러난 약점이 어디서 오는지와 배포할 때 무엇을 덧붙일지를 확인한다. 아직 아무도 보지 않은 기간(2026년 8월 이후 승차 자료)에서 다시 검증해야 한다.

**그 검증을 했다.** v2를 2026년 8~11월 공연일 39일로 한 번 채점한 사전 기록·스크립트·결과가 [heldout_2026_autumn/](heldout_2026_autumn/README.md)에 있다.

| 분석 | 확인한 것 | 결과 파일 |
|---|---|---|
| [최근 수준 반영](level_tracking.py) | 예측이 일관되게 낮았다. 직전 6개월의 사전 예측과 관측의 비율(저녁별 중앙값)을 다음 달 관객 반응에 곱하면 2026년 총량 편향이 −18.3% → −12.0%, 피크 부족분이 31.4% → 26.6%가 된다. 판본의 `level.json`과 `calibration.json`을 이 분석이 쓴다 | [level_tracking.json](level_tracking.json) |
| [5분 승차 대조](station_5min_check.py) | 학습 종료 석 달 뒤인 2026-11-07, 올림픽공원역 5·9호선. 20~22시대 합계 실제 10,183명 대 예측 8,878명, 급증 시작 20:50 대 20:55, 가장 붐빈 5분 1,105명 대 539명 | [station_5min_check.json](station_5min_check.json) · [5분 표](station_5min_check.csv) |
| [한 저녁의 도착 곡선](within_event_curve.py) | 시간 단위로 맞춘 곡선은 종료 시각이 다른 저녁들의 평균이라 한 저녁보다 완만하다. 저녁마다 종료 시각이 달라질 수 있게 두면 곡선이 좁아지고, 5분 자료가 있는 하루에서 정점 1,005명(실제 1,064명)을 낸다 | [within_event_curve.json](within_event_curve.json) |
| [주차 출구 적용](parking.py) | 같은 규칙을 차량 출구 7곳의 출차 대수에 적용. 합계와 정점은 개선, 출구별 시간 오차는 악화. 수요표에는 반영하지 않았다 | [parking.json](parking.json) |

| [2026년 발권 수로 본 것](tickets_2026.py) | KSPO DOME 회차당 발권 수는 2025년과 2026년이 거의 같다. 2026년 과소예측은 관객 수가 아니라 관객 1명당 승차 비율의 변화다. 2025년까지의 이력으로 만든 관객 수 추정을 2026년 발권 수로 시험했고, v1에 실제 발권 수를 넣은 결과도 냈다 | [tickets_2026.json](tickets_2026.json) |

| [시각 변수 후보](v3_candidates.py) | v2 채점에서 예측이 실제보다 평균 26분 늦었다. 페스티벌 길이, 최근 석 달의 치우침, 관객층별 시각을 v2에 하나씩 더해 20개월을 순차 예측했다. 가을 이전과 가을 모두에서 도움이 되는 것이 없어 v3에 넣지 않았다 | [v3_candidates.json](v3_candidates.json) |
| [한 저녁의 5분 곡선](station_curve_5min.py) | 올림픽공원역 5분 승차로 KSPO DOME 저녁의 도착 곡선을 다시 맞춘다. 판본 폴더를 인자로 받아 `single_show_curve.json`을 쓴다 | 판본 안 |
| [관객층을 다르게 다루면](audience_candidates.py) | 지금 규칙의 중장년 효과(−36%)는 발권 수가 없는 2023~2024년과 섞여 배운 값이다. 발권 수가 있는 해로만 배우면 −17%다. 관객층을 빼거나, 해를 나눠 배우거나, 출연자의 과거 승차 비율을 더한 아홉 가지를 20개월 순차 예측으로 비교했다. 행사일 238일의 총량 오차는 어느 것도 지금 규칙에서 ±0.2%p 밖으로 벗어나지 않았다. 중장년 공연일 41일의 시간별 오차는 관객층을 뺀 쪽이 14명 컸다(+1.6 ~ +26.4). 둘을 후보로 걸어 다음 채점에서 가린다 | [audience_candidates.json](audience_candidates.json) |
| [새 자료로 계속 학습하면](continual_learning.py) | 자료가 있는 20개월을 ‘매월·분기·반기·연 1회 갱신, 갱신 없음’으로 다시 돌려 봤다. 갱신을 멈춘 모델은 1년 안에 총량 편향이 두 배가 되고, 분기 갱신은 매월과 거의 같다. 최근 12개월만 써도 전체 이력과 같다. 출연자 이력이 있는 KSPO DOME 회차는 43%에서 매월 갱신 시 49%로 늘고 발권 수 오차가 19% 작다. 총량의 80% 범위는 최근 빗나간 횟수로 폭을 조정하면 포함률이 74%에서 79%가 된다. 재학습 20초, 하루치 수집 22초. 모두 이미 본 기간의 재현이다 | [continual_learning.json](continual_learning.json) |

이 분석들을 바탕으로 만든 판본이 [v2](../../artifacts/egress-response/rail-2026-11-v2/MODEL_CARD.md)와 [v3](../../artifacts/egress-response/rail-2026-11-v3/MODEL_CARD.md)다. 스크립트에 판본 폴더를 인자로 주면 결과를 그 판본 안에 쓴다.

```sh
PYTHONPATH=. python experiments/response_model/tickets_2026.py
PYTHONPATH=. python experiments/response_model/station_5min_check.py
PYTHONPATH=. python experiments/response_model/within_event_curve.py
PYTHONPATH=. python experiments/response_model/level_tracking.py
PYTHONPATH=. python experiments/response_model/parking.py
python -m olpark_demand.response_report --bundle artifacts/egress-response/rail-2026-11
```

`within_event_curve.py`는 `station_5min_check.py`가 만든 5분 표를 읽으므로 그 뒤에 실행한다. `level_tracking.py`는 판본의 예측 범위 파일을 다시 쓴다.
