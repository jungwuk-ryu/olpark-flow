import { Sidebar } from './components/layout/Sidebar';
import { TopBar } from './components/layout/TopBar';
import { PageHeader } from './components/layout/PageHeader';
import { Card, CardHeader, Pending } from './components/ui/Card';
import { Icon } from './components/ui/Icon';
import { Button } from './components/ui/Button';

/**
 * 1단계 레이아웃 뼈대.
 * 카드 배치·간격만 확정하고, 숫자와 내용은 3단계 이후 엔진 결과로 채운다.
 */

const DATA_SOURCES = [
  '한국체육산업개발 공연정보',
  '차량입출차 연계',
  '실시간 주차현황',
  '주차장 혼잡도',
  '서울시 실시간 인구',
  '서울시 도보 네트워크',
  '서울교통공사 승하차',
];

const EVENT_CELLS = ['분석 행사', '종료 예정', '기준 보행 경로', '분석 상태'];
const KPI_LABELS = ['예상 최대 대기 인원', '대기 + 추가 보행', '관객 1인당 추가 시간', '대기열 해소 예상'];

export default function App() {
  return (
    <div className="flex min-h-screen min-w-[1280px]">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar date={new Date(2026, 10, 21)} />
        <main className="mx-auto flex w-full max-w-[1680px] flex-col gap-5 px-8 pt-7 pb-10">
          {/* 운영 개요 */}
          <div id="overview" className="flex scroll-mt-[84px] flex-col gap-5">
            <PageHeader />

            <div className="flex flex-col gap-2.5">
              <Card className="grid grid-cols-[minmax(0,1fr)_250px_200px_216px]">
                {EVENT_CELLS.map((label, i) => (
                  <div key={label} className={`flex flex-col gap-1.5 px-5 py-4 ${i < 3 ? 'border-r border-line' : ''}`}>
                    <span className="text-[12px] leading-[18px] text-ink-2">{label}</span>
                    <span className="h-[22px] w-28 rounded bg-subtle" aria-hidden="true" />
                  </div>
                ))}
              </Card>
              <div className="flex items-center gap-2">
                <span className="inline-flex h-8 items-center gap-2 rounded-full border border-line bg-card px-2.5 text-[13px] text-ink-2">
                  <span className="text-accent">
                    <Icon name="sparkles" size={14} />
                  </span>
                  장르 추정 칩 · 5단계
                </span>
              </div>
            </div>

            <Card className="grid grid-cols-4">
              {KPI_LABELS.map((label, i) => (
                <div key={label} className={`flex flex-col gap-2 px-6 py-5 ${i < 3 ? 'border-r border-line' : ''}`}>
                  <span className="text-[13px] leading-5 font-medium text-ink-2">{label}</span>
                  <span className="h-11 w-24 rounded bg-subtle" aria-hidden="true" />
                  <span className="h-[18px] w-40 rounded bg-subtle" aria-hidden="true" />
                </div>
              ))}
            </Card>

            <Card className="flex gap-5 px-6 py-[18px]">
              <div className="flex w-[150px] shrink-0 flex-col gap-1.5">
                <span className="flex items-center gap-1.5 text-[15px] font-bold">
                  <span className="text-accent">
                    <Icon name="sparkles" size={16} />
                  </span>
                  AI 브리핑
                </span>
                <span className="text-[12px] text-ink-2">계산 결과로 생성</span>
              </div>
              <div className="flex-1">
                <Pending label="계산 결과 요약 문장 2~3개 · 5단계" height={72} />
              </div>
            </Card>
          </div>

          {/* 지도 · 설정 */}
          <div className="grid grid-cols-[minmax(0,1fr)_356px] items-stretch gap-5 2xl:grid-cols-[minmax(0,1fr)_380px]">
            <div className="flex min-w-0 flex-col gap-5">
              <Card id="flow-map" className="flex scroll-mt-[84px] flex-col overflow-hidden">
                <CardHeader
                  className="px-6 pt-5 pb-4"
                  title="귀가 흐름 지도"
                  description="방면별 경로와 시간대별 병목을 확인합니다"
                  actions={
                    <div className="flex rounded-lg bg-page p-[3px] text-[13px]" role="group" aria-label="운영안 선택">
                      <span className="rounded-md px-3.5 py-1.5 text-ink-2">현재 운영안</span>
                      <span className="rounded-md border border-line bg-card px-3.5 py-1.5 font-bold">분산 운영안</span>
                    </div>
                  }
                />
                <div className="flex h-[560px] items-center justify-center bg-map-ground text-[13px] text-ink-2">
                  지도 · 4단계 (MapLibre + 브이월드)
                </div>
                <div className="flex items-center gap-4 border-t border-line px-6 py-3.5 text-[13px] text-ink-2">
                  예측 시점
                  <span className="h-1 flex-1 rounded bg-line" />
                  종료 후 0~60분
                </div>
              </Card>

              <Card className="flex flex-col gap-3 p-6 pt-5">
                <CardHeader
                  title="대기 인원 추이"
                  description="퇴장 시작 후 시간에 따라 쌓이는 예상 대기열"
                />
                <Pending label="현재 운영안 vs 분산 운영안 · 3단계" height={240} />
              </Card>

              <Card className="flex flex-1 flex-col gap-3.5 p-6 pt-5">
                <CardHeader
                  title="현장 통제"
                  description="공지에서 추출한 통제 구간을 계산에 반영합니다"
                  actions={
                    <>
                      <Button icon="notice" disabled>
                        공지에서 통제 구간 불러오기
                      </Button>
                      <Button icon="plus" disabled>
                        수동 추가
                      </Button>
                    </>
                  }
                />
                <Pending label="통제 구간 카드 · 5단계" height={180} />
              </Card>
            </div>

            <div id="dispersal" className="flex scroll-mt-[84px] flex-col gap-5">
              <Card className="flex flex-col gap-5 p-6 pt-5">
                <CardHeader
                  title="분산 운영 설정"
                  description="조건을 바꾸면 운영안을 다시 계산합니다"
                  actions={
                    <span className="text-ink-2">
                      <Icon name="sliders" size={20} />
                    </span>
                  }
                />
                <Pending label="슬라이더 · 경로 토글 · 결과 요약 · 3단계" height={420} />
                <Button variant="primary" icon="refresh" block disabled>
                  분산 운영안 다시 계산
                </Button>
                <Pending label="방면별 안내 비중 · 3단계" height={240} />
              </Card>

              <Card className="flex flex-1 flex-col gap-4 p-6 pt-5">
                <CardHeader
                  title="차량·주차"
                  description="주차장 P1~P7에서 차량 출구까지의 대기"
                  actions={
                    <span className="text-ink-2">
                      <Icon name="car" size={20} />
                    </span>
                  }
                />
                <Pending label="차량 비율 · 출구별 대기 · 배정 비율 · 6단계" height={320} />
              </Card>
            </div>
          </div>

          {/* 분석 결과 */}
          <div id="results" className="flex scroll-mt-[84px] flex-col gap-5">
            <div className="grid grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)] gap-5">
              <Card className="flex flex-col gap-4 p-6 pt-5">
                <CardHeader
                  title="운영안 비교"
                  description="같은 관객 수와 통로 조건에서 계산한 결과"
                  actions={
                    <Button icon="copy" disabled>
                      안내표 복사
                    </Button>
                  }
                />
                <Pending label="비교 표 · 3단계" height={320} />
              </Card>
              <Card className="flex flex-col gap-4 p-6 pt-5">
                <CardHeader
                  title="현장 안내 실행표"
                  description="방면별 분산 안내를 위한 실행 순서"
                  actions={
                    <Button icon="copy" disabled>
                      복사
                    </Button>
                  }
                />
                <Pending label="실행 단계 · 7단계" height={320} />
              </Card>
            </div>

            <Card className="flex flex-col gap-4 p-6 pt-5">
              <CardHeader
                title="분석 기록"
                description="이 브라우저에 저장한 운영안"
                actions={
                  <span className="text-ink-2">
                    <Icon name="history" size={20} />
                  </span>
                }
              />
              <Pending label="저장한 운영안 목록 · 7단계" height={96} />
            </Card>
          </div>

          <footer className="flex flex-col gap-2.5 pt-2 text-[12px] leading-[18px] text-ink-2">
            <p>
              수치는 선택한 조건으로 계산한 예측값입니다. 보행 경로와 통과 용량은 시연용 가정값이며, 공원 내 실제 통행
              가능 구간은 운영 전 확인이 필요합니다.
            </p>
            <p className="flex flex-wrap items-center gap-x-4 gap-y-1.5">
              <span className="font-bold text-ink">데이터 출처</span>
              {DATA_SOURCES.map((s) => (
                <span key={s} className="underline decoration-line-strong">
                  {s}
                </span>
              ))}
            </p>
          </footer>
        </main>
      </div>
    </div>
  );
}
