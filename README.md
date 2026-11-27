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

**예측 서버를 직접 실행하려면 Git에 없는 데이터가 필요합니다.** 현재 판본(`rail-2026-11-v3`)은 가공 데이터 14개와 같은 판본의 발권·출연자 이력 CSV 2개로 실행을 확인했습니다. 협업자는 아래 목록의 파일을 데이터 담당자에게 받아 지정 경로에 배치하세요. 준비된 데이터로 예측을 실행하거나 공원의 공연 목록을 갱신할 때는 API 키가 필요 없습니다. [예측 서버용 파일 16개와 배치 경로](ml/data/README.md#예측-서버-실행-자료)

KOPIS 발권 통계를 직접 수집할 때는 별도로 발급받은 `KOPIS_API_KEY`를 수집기를 실행할 터미널에 설정합니다. [API 키 발급 안내 및 PowerShell·Bash·zsh 설정 방법](ml/data/README.md#인증키)

아래 명령은 저장소 루트에서 시작합니다. API만 사용하면 Python 3.12와 모델 자료로 충분합니다. 같은 서버에서 화면도 제공하려면 먼저 루트에서 `pnpm install --frozen-lockfile`과 `pnpm build`를 실행하세요. 시작 후 [서버 상태](http://127.0.0.1:4173/api/status)에서 `ready: true`와 `lastFailure: null`을 확인합니다.

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

팀 서버가 이미 있으면 개발 화면만 연결할 수 있습니다. `pnpm dev`를 실행할 터미널에서 PowerShell은 `$env:OLPARK_API = 'http://호스트:포트'`, Bash·zsh는 `export OLPARK_API='http://호스트:포트'`로 지정하세요. 이 값은 예측 서버의 기본 주소입니다.

### 관객층 자동 추측과 수동 선택

예측 서버를 실행할 셸에 아래 키 중 하나를 설정하고 서버를 시작하세요. 미분류 공연을 처음 열면 모델이 공식 공연명, 관람 연령, 소개, 제작사·주최사, 포스터와 웹 검색 결과를 바탕으로 일곱 관객층 중 하나를 선택합니다. 소개 HTML은 본문 텍스트로 바꾸고, 공식 사이트에 있는 소개 이미지는 최대 3개까지 모델에 이미지로 전달합니다. 세로로 긴 이미지는 글자가 읽히도록 나눠 보냅니다.

| 서버 환경변수 | 기본 모델 |
| --- | --- |
| `OPENAI_API_KEY` | `gpt-6.1-luna`의 제공 여부를 먼저 조회하고, 해당 모델이 없으면 `gpt-6-luna` |
| `ANTHROPIC_API_KEY` | `claude-haiku-5-5` |
| `GEMINI_API_KEY` 또는 `GOOGLE_API_KEY` | `gemini-4-hydrogen` |

키가 여러 개면 위 표의 순서로 선택합니다. `OLPARK_AUDIENCE_PROVIDER`로 `openai`, `anthropic`, `gemini` 중 하나를, `OLPARK_AUDIENCE_MODEL`로 모델 ID를 지정할 수 있습니다.

관리자는 공연 정보 아래 태그를 눌러 직접 선택할 수 있습니다. 처음 저장할 때 서버의 `OLPARK_ADMIN_TOKEN`을 입력합니다. 설정하지 않으면 서버가 키를 발급해 `ml/data/runtime/audience/admin.token`에 보관합니다. 수동 선택은 진행 중인 추측을 취소하고 해당 공연의 모든 회차에 반영됩니다.

공식 공연 ID별 분류는 `ml/data/runtime/audience/audience.sqlite3`에 저장합니다. 여러 서버 프로세스가 같은 머신에서 동작하면 같은 디렉터리를 사용하세요. `OLPARK_AUDIENCE_DIR`로 영구 저장 경로를 지정할 수 있습니다. 기존 수작업 분류와 관리자 선택에서 출연자·공연명·제작 주체가 겹치는 사례를 최대 다섯 개 검색합니다. 모델은 이번 공연과 맞는 사례만 참고할 수 있습니다. 호출 실패는 화면에서 관리자가 다시 시도할 수 있습니다. 공연별 원본 기록을 남기고 관련된 기억만 꺼내 쓰는 방식은 Codex의 메모리 읽기 경로를 참고했습니다. [Codex 메모리 구현](https://github.com/openai/codex/tree/main/codex-rs/memories)

[퇴장 수요 예측 데이터와 수집 범위](ml/data/README.md)

[파이썬 쪽 폴더 구성과 설치](ml/README.md) · [퇴장 수요 모델 학습·조회 방법](ml/olpark_demand/README.md) · [행사 반응 모델 v3 모델 카드](ml/artifacts/egress-response/rail-2026-11-v3/MODEL_CARD.md) · [2025년 학습 모델과 검증 결과](ml/artifacts/egress-demand/2025-d1/MODEL_CARD.md) · [연구·운영 설계](docs/design/egress-demand/README.md)

[공연별 방문·귀가 비중과 5분 보행망 입력 사용법](ml/olpark_demand/README.md#공연별-방문귀가-비중과-보행망-입력) · [실측 검증](ml/artifacts/egress-od/validation/README.md)

[대시보드에 공연과 예측을 주는 서버](ml/olpark_demand/README.md#대시보드에-공연과-예측-주기) · [화면과 주고받는 값의 약속](docs/data-contract.md) · [계산 엔진](src/engine/README.md)

## OS별 자동 검사

GitHub Actions의 **OS별 검사**는 Ubuntu·Windows 각각에서 Node.js 24/pnpm 11.12.0으로 데이터 검사·화면 테스트·프로덕션 빌드를 실행하고, 별도 작업에서 Python 3.12로 `ml/tests`의 unittest를 실행합니다. PR과 main 푸시 때 자동 실행하며 Actions에서 수동으로도 실행할 수 있습니다. Python은 UTF-8 모드로 고정합니다.

비공개 가공 자료와 API 키를 CI에 넣지 않습니다. 자료가 필요한 파이썬 검사는 기존 테스트의 skip 조건에 따라 건너뜁니다. 따라서 CI 통과는 새 체크아웃의 단위검사·빌드 호환성을 뜻하며, 실제 자료를 사용하는 모델 예측이나 화면의 브라우저별 시각 검증까지 보증하지 않습니다. 각 OS의 실행 결과와 건너뛴 검사 수는 Actions 로그에서 확인합니다.
