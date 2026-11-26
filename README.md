# olpark-flow

국민체육진흥공단이 공연 종료 뒤 어디에서 언제 보행 병목이 생길지 예측하고, 가용한 안전요원을 어느 지점에 언제 배치할지 판단하도록 돕는 것이 목표다. 퇴장 수요 모델은 보행 네트워크가 계산에 사용할 공연장 출구·목적지·시간대별 관객 수를 추정하는 역할을 맡는다.

## 대시보드 실행

Windows PowerShell, Linux, macOS에서 저장소 루트에 Node.js 24와 pnpm 11을 준비한 뒤 실행합니다.

```sh
pnpm install --frozen-lockfile
pnpm dev
```

터미널에 표시된 주소(기본 `http://localhost:5173`)를 엽니다. 예측 서버가 없으면 화면에 **시연용 데이터**가 표시되며, 저장소에 포함된 공연과 보행로로 시뮬레이션을 실행합니다. 지도는 WebGL 2 지원 브라우저를 사용하며 기본 배경지도는 인터넷으로 불러옵니다. 시연에는 Python이나 API 키가 필요하지 않습니다.

## 실제 공연 예측

팀의 예측 서버에 접속할 수 있으면 로컬 Python 설치 없이 실제 예측을 볼 수 있습니다. 루트 `.env.local`에 `OLPARK_API=http://호스트:포트`를 지정하고 `pnpm dev`를 다시 시작하세요. 주소에는 `/api`를 붙이지 않습니다.

예측 서버도 직접 실행하려면 Python 3.12와 [모델 실행 자료](ml/olpark_demand/README.md)가 필요합니다. 담당자에게 `ml/data/prepared/`와 같은 모델 판본의 `attendance_history.csv`, `act_history.csv`를 받거나 원자료로 재생성하세요. 이력 CSV의 위치는 `ml/artifacts/egress-response/<판본>/`입니다. 같은 폴더의 `development_forecasts.parquet`는 과거 승차 예측 검증 지표를 표시할 때 사용합니다.

자료를 배치한 뒤 루트에서 `pnpm build`를 실행하고 아래 명령으로 서버를 시작합니다. 대시보드 주소는 `http://127.0.0.1:4173/`이며 `/api/status`의 `ready`가 `true`이면 예측을 사용할 수 있습니다.

**Windows PowerShell**

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r ml/requirements.txt
cd ml
..\.venv\Scripts\python.exe -X utf8 -m olpark_demand.server
```

**Linux · macOS**

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r ml/requirements.txt
cd ml
../.venv/bin/python -X utf8 -m olpark_demand.server
```

[퇴장 수요 예측 데이터와 수집 범위](ml/data/README.md)

[파이썬 쪽 폴더 구성과 설치](ml/README.md) · [퇴장 수요 모델 학습·조회 방법](ml/olpark_demand/README.md) · [행사 반응 모델 v3 모델 카드](ml/artifacts/egress-response/rail-2026-11-v3/MODEL_CARD.md) · [2025년 학습 모델과 검증 결과](ml/artifacts/egress-demand/2025-d1/MODEL_CARD.md) · [연구·운영 설계](docs/design/egress-demand/README.md)

[공연별 방문·귀가 비중과 5분 보행망 입력 사용법](ml/olpark_demand/README.md#공연별-방문귀가-비중과-보행망-입력) · [실측 검증](ml/artifacts/egress-od/validation/README.md)

[대시보드에 공연과 예측을 주는 서버](ml/olpark_demand/README.md#대시보드에-공연과-예측-주기) · [화면과 주고받는 값의 약속](docs/data-contract.md) · [계산 엔진](src/engine/README.md)
