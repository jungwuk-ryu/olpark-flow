import { useEffect } from 'react';
import { Sidebar } from './components/layout/Sidebar';
import { TopBar } from './components/layout/TopBar';
import { PageHeader } from './components/layout/PageHeader';
import { DataSourceNote } from './components/sections/DataSourceNote';
import { SimulationSection } from './components/sections/SimulationSection';
import { EventBriefCard } from './components/sections/EventBriefCard';
import { ActionCard } from './components/sections/ActionCard';
import { EvidenceCard } from './components/sections/EvidenceCard';
import { loadDataBundle } from './api';
import { useScenario } from './store/scenario';
import { Button } from './components/ui/Button';

/**
 * 화면의 중심은 보행 시뮬레이션이다: 공연을 고르면 공원의 모든 보행로에 사람 흐름이 그려지고,
 * 운영자가 지도에서 길을 누르고 막기·장애물·안전요원을 놓으면 바로 다시 계산한다.
 * 그 아래에 그 공연의 예측(누가 언제 어디로), 시간순 할 일, 예측을 믿을 근거를 둔다.
 */

const DATA_SOURCES = [
  '한국체육산업개발 공연정보',
  '차량입출차 연계',
  '실시간 주차현황',
  '주차장 혼잡도',
  '서울시 실시간 인구',
  '서울시 도보 네트워크',
  '서울교통공사 승하차',
  '서울시 교통카드 승하차 (T-DATA)',
  'KOPIS 공연예술통합전산망',
];

export default function App() {
  const sessionKey = 'olpark.workspace.closed';
  if (sessionStorage.getItem(sessionKey)) {
    return (
      <main className="grid min-h-screen place-items-center p-6">
        <section className="flex w-full max-w-sm flex-col gap-5 rounded-[var(--radius-card)] border border-line bg-card p-7">
          <p className="text-[13px] font-bold text-accent">OLPARK FLOW</p>
          <h1 className="text-xl font-bold">운영 세션이 종료되었습니다.</h1>
          <Button variant="primary" onClick={() => {
            sessionStorage.removeItem(sessionKey);
            window.location.reload();
          }}>운영 화면 열기</Button>
        </section>
      </main>
    );
  }
  return <Dashboard onLogout={() => {
    sessionStorage.setItem(sessionKey, 'true');
    window.location.reload();
  }} />;
}

function Dashboard({ onLogout }: { onLogout: () => void }) {
  const init = useScenario((s) => s.init);
  // 예측을 만든 날짜. 불러오기 전에는 시연 기준일을 보인다
  const generatedAt = useScenario((s) => s.data?.predictions.generatedAt);
  const source = useScenario((s) => (s.data ? (s.data.source?.kind === 'server' ? 'server' : 'mock') : 'loading'));
  useEffect(() => {
    let alive = true;
    loadDataBundle().then((d) => alive && init(d));
    return () => {
      alive = false;
    };
  }, [init]);

  return (
    <div className="flex min-h-screen min-w-[1280px]">
      <Sidebar onLogout={onLogout} />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar date={generatedAt ? new Date(generatedAt) : new Date(2026, 10, 21)} source={source} />
        <main className="mx-auto flex w-full max-w-[1680px] flex-col gap-5 px-8 pt-7 pb-10">
          <div className="flex flex-col gap-3">
            <PageHeader />
            <div className="flex flex-wrap items-center gap-2">
              <DataSourceNote />
            </div>
          </div>

          <SimulationSection />

          <EventBriefCard />
          <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] items-stretch gap-5">
            <ActionCard />
            <EvidenceCard />
          </div>

          <footer className="flex flex-col gap-2.5 pt-2 text-[12px] leading-[18px] text-ink-2">
            <p>
              보행로 폭과 통행 용량에는 가정값이 포함됩니다. 현장 운영 전 실제 통행 가능 구간을 확인하세요.
            </p>
            <p className="flex flex-wrap items-center gap-x-4 gap-y-1.5">
              <span className="font-bold text-ink">데이터 출처</span>
              {DATA_SOURCES.map((s) => (
                <span key={s} className="underline decoration-line-strong">
                  {s}
                </span>
              ))}
              <a
                href="https://www.openstreetmap.org/copyright"
                target="_blank"
                rel="noreferrer"
                className="underline decoration-line-strong hover:text-ink"
              >
                보행로 © OpenStreetMap contributors (ODbL)
              </a>
              <a href="https://openfreemap.org" target="_blank" rel="noreferrer" className="underline decoration-line-strong hover:text-ink">
                배경 지도 OpenFreeMap © OpenMapTiles
              </a>
            </p>
          </footer>
        </main>
      </div>
    </div>
  );
}
