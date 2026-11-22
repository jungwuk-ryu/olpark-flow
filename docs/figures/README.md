# 기획서용 그림

`geo/validate/make_figures.py`로 만든 1600×900 PNG, AppleGothic 글꼴이다.

| 파일 | 내용 | 핵심 숫자 |
|---|---|---|
| `direction-share.png` | 실제 지하철 방면 비율과 목업 예측 | 관측 A/B/C 93/3/4%, 목업 72/19/9% |
| `arrival-concentration.png` | 역 도착 시간 집중도 | 실제 상위 1/2시간 75/95%, 계산 퇴장 22분 89/100%, 60분 78/99%, 80분 71/97% |
| `sensitivity-reduction.png` | 12개 경우의 부담 감소율 | 6~27%, 기준값 14% |

입력은 `docs/methods/network.md` 6장과 `geo/validate/sensitivity-result.txt`다. 값·제목·한계를 그림 안에 함께 적었다. 관측은 집계 자료이며, 계산 결과를 현장 효과 실측으로 표현하지 않는다.

재생성: matplotlib이 설치된 macOS Python에서 `python3 geo/validate/make_figures.py`. AppleGothic이 없으면 중단한다. 버전: matplotlib 3.11.2, numpy 2.5.5.
