# ml: 퇴장 수요 모델

올림픽공원 공연이 끝난 뒤 관객이 언제, 어느 역과 주차장으로, 몇 명 나가는지를 전날 예측하는 파이썬 쪽 작업이 모두 이 폴더에 있다. 화면은 공연 목록과 예측을 이 폴더의 [서버](olpark_demand/README.md#대시보드에-공연과-예측-주기)에서 받는다.

| 폴더 | 내용 |
|---|---|
| [olpark_demand/](olpark_demand/README.md) | 패키지. 수집, 학습, 수요표 생성, 갱신 명령과 사용법 |
| [artifacts/](artifacts/egress-response/rail-2026-11-v3/MODEL_CARD.md) | 학습한 판본과 모델 카드 |
| [experiments/](experiments/response_model/README.md) | 검증 실험의 스크립트와 결과 |
| tests/ | 테스트 |
| examples/ | 입력 예제 |
| [data/](data/README.md) | 자료의 출처와 수집 명세. 원본과 가공 파일은 저장소에 올리지 않는다 |

## 준비

```sh
cd ml
python3.12 -m venv ../.venv && source ../.venv/bin/activate
python -m pip install -r requirements.txt
```

명령은 모두 이 폴더에서 실행한다. 화면 쪽 파일(`../src/data`의 보행망과 접근 등록부, `../docs/design`의 교환 형식)은 패키지가 저장소에서 직접 찾는다.

```sh
PYTHONPATH=. python -m unittest discover -s tests                     # 자료가 없으면 자료가 필요한 테스트는 건너뛴다
python -m olpark_demand.egress build --request examples/egress_planned_request.json --output /tmp/olpark-planned.json
python -m olpark_demand.update_cycle plan                             # 새 자료로 갱신할 때 받을 날짜와 용량
python -m olpark_demand.server                                        # 화면과 /api를 127.0.0.1:4173에 띄운다 (자료 필요)
```

수요표 생성과 학습에는 `data/`의 자료가 필요하다. 받는 방법은 [자료 문서](data/README.md)에, 명령별 설명은 [패키지 문서](olpark_demand/README.md)에 있다.
