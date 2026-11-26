import { lazy, Suspense, useEffect, useMemo, useState } from 'react';
import { Area, CartesianGrid, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { AudienceTags } from './AudienceTags';
import { Card, CardHeader } from '../ui/Card';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';
import { Icon } from '../ui/Icon';
import { Slider } from '../ui/Slider';
import { StatusLabel } from '../ui/StatusLabel';
import { clockTimes, walkCapacityPerMin, type WalkGraph, type WalkMode } from '../../engine';
import { formatClock, formatClockRange, formatDay, formatNumber } from '../../lib/format';
import { endWindowText, PARAM_LIMITS, type EndTime } from '../../model/scenario';
import { busiestEdges, edgeGeometry, hasControls, KIND_NAME, nearestPlace, placeText, runWalk, type ControlKind, type OffMapMode } from '../../model/walkplan';
import { selectEvent, selectPrediction, useEndTime, useScenario } from '../../store/scenario';
import { useWalk } from '../../store/walk';
import type { BasemapState } from '../map/WalkMap';
import { RAMP_CSS } from '../map/flowScale';
import { CONTROL_ICON, CONTROL_NAME, EdgeChoice } from './EdgeChoice';
import { EventPicker } from './EventPicker';

// 지도 라이브러리와 보행로 자료는 크기가 커서 이 화면이 그려질 때 따로 받는다
const WalkMap = lazy(() => import('../map/WalkMap'));
const loadGraph = () => import('../../data/walkways.json').then((m) => m.default as unknown as WalkGraph);

const LAST_MINUTE = 90;
const MINUTE_MS = 60_000;

const STEPS = ['공연 선택', '보행로 선택', '통제·배치 조건별 결과 비교'];
/** 지도에서 혼잡한 길에 두르는 테두리 색 */
const HALO = [
  { status: '보통', tone: 'bg-normal' },
  { status: '병목', tone: 'bg-bottleneck' },
  { status: '위험', tone: 'bg-danger' },
] as const;
const MODE_NAME: Record<WalkMode | OffMapMode, string> = { rail: '지하철', bus: '버스', car: '차량', walk: '도보 귀가', other: '픽업 등' };

/** 종료 후 minute분의 시각. 종료 시각에 범위가 있으면 괄호로 함께 적는다 */
function clockText(end: EndTime | null, minute: number, ranged = true): string {
  if (!end) return `종료 +${minute}분`;
  const t = clockTimes(end.expectedAt, minute, end.window ?? undefined);
  return end.window && ranged ? `${formatClock(t.at)} (${formatClockRange(t.earliest, t.latest)})` : formatClock(t.at);
}

function Stat({ label, value, note, tone }: { label: string; value: string; note?: string; tone?: 'good' | 'bad' }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[12px] leading-[18px] text-ink-2">{label}</span>
      <span className="text-[20px] leading-7 font-bold text-ink">{value}</span>
      {note && <span className={`text-[12px] leading-[18px] font-medium ${tone === 'good' ? 'text-accent' : tone === 'bad' ? 'text-bottleneck' : 'text-ink-2'}`}>{note}</span>}
    </div>
  );
}

export function SimulationSection() {
  const data = useScenario((s) => s.data);
  const event = useScenario(selectEvent);
  const prediction = useScenario(selectPrediction);
  const params = useScenario((s) => s.params);
  const setParam = useScenario((s) => s.setParam);
  const end = useEndTime();
  const { controls, history, minute, playing, selected, pickAt, focus } = useWalk();
  const walk = useWalk.getState;
  const [graph, setGraph] = useState<WalkGraph | null>(null);
  const [picking, setPicking] = useState(false);
  const [basemap, setBasemap] = useState<BasemapState>('loading');
  const [unsupported, setUnsupported] = useState(false);

  useEffect(() => {
    let alive = true;
    void loadGraph().then((g) => alive && setGraph(g));
    return () => {
      alive = false;
    };
  }, []);
  // 다른 공연을 고르면 놓은 것과 보던 시점을 처음으로 돌린다
  useEffect(() => walk().reset(), [event?.id, walk]);
  // 재생: 0.35초에 1분씩
  useEffect(() => {
    if (!playing) return;
    const timer = setInterval(() => {
      const now = walk().minute ?? 0;
      if (now >= LAST_MINUTE) walk().setPlaying(false);
      else walk().setMinute(now + 1);
    }, 350);
    return () => clearInterval(timer);
  }, [playing, walk]);
  // Esc로 선택 상자를 닫는다
  useEffect(() => {
    if (!selected) return;
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && walk().select(null);
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [selected, walk]);

  const run = useMemo(
    () => (data && graph && event && prediction && params ? runWalk(data, graph, event, prediction, params, controls) : null),
    [data, graph, event, prediction, params, controls],
  );
  const busy = useMemo(() => (graph && run ? busiestEdges(graph, run.current, 8) : []), [graph, run]);
  const edgeById = useMemo(() => new Map(graph?.edges.map((e) => [e.id, e])), [graph]);
  const midOf = (edgeId: string) => (graph ? edgeGeometry(graph).mid.get(edgeId) : undefined);
  const whereOf = (edgeId: string) => (graph && midOf(edgeId) ? placeText(nearestPlace(graph, midOf(edgeId)!)) : '');
  const focusOn = (edgeId: string) => {
    const at = midOf(edgeId);
    if (at) walk().focusOn(edgeId, at);
  };

  const placed: { id: string; kind: ControlKind }[] = [
    ...controls.blocked.map((id) => ({ id, kind: 'block' as const })),
    ...Object.keys(controls.narrowed).map((id) => ({ id, kind: 'narrow' as const })),
    ...controls.staffed.map((s) => ({ id: s.edgeId, kind: 'staff' as const })),
  ].filter((x) => edgeById.has(x.id));
  const edited = hasControls(controls);
  const now = run?.current.totals;
  const before = run?.baseline.totals;
  const maxStationShare = params ? Math.max(PARAM_LIMITS.stationShare.min, Math.floor((1 - params.vehicleRatio) * 100 + 1e-9) / 100) : 1;
  const stations = graph?.destinations.filter((d) => d.mode === 'rail') ?? [];
  const chosen = selected ? edgeById.get(selected) : undefined;
  const t = minute === null ? null : minute - 1;
  const viewing = minute === null ? '전체 시간대' : `${clockText(end, minute, false)} · 종료 +${minute}분`;
  const endMs = end ? Date.parse(end.expectedAt) : NaN;
  const chart = run
    ? Array.from({ length: LAST_MINUTE + 1 }, (_, x) => ({
        x,
        now: x === 0 ? 0 : (run.current.queueTotal[x - 1] ?? 0),
        ...(edited ? { before: x === 0 ? 0 : (run.baseline.queueTotal[x - 1] ?? 0) } : {}),
      }))
    : [];
  const waitEach = (totals: NonNullable<typeof now>) => (totals.people > 0 ? totals.queueMinutesTotal / totals.people : 0);
  const delta = (a: number, b: number, unit: string, digits = 0): { note: string; tone?: 'good' | 'bad' } => {
    const d = a - b;
    if (Math.abs(d) < (digits ? 0.05 : 0.5)) return { note: '변경 전과 동일' };
    return { note: `변경 전 대비 ${formatNumber(Math.abs(d), digits)}${unit} ${d < 0 ? '감소' : '증가'}`, tone: d < 0 ? 'good' : 'bad' };
  };
  const detailOf = (x: { id: string; kind: ControlKind }) => {
    if (x.kind === 'narrow') return `유효 폭 ${formatNumber(controls.narrowed[x.id]!, 1)}m`;
    if (x.kind === 'staff') {
      const to = controls.staffed.find((s) => s.edgeId === x.id)?.sendTo;
      return to ? `${stations.find((d) => d.id === to)?.name ?? '대체 역'} 방면 안내` : '우회 안내';
    }
    return '전면 통제';
  };

  const popup =
    chosen && run && data ? (
      <EdgeChoice
        edge={chosen}
        series={run.current.edges[chosen.id]}
        where={whereOf(chosen.id)}
        minuteIndex={t}
        capacityPerMin={walkCapacityPerMin({ widthM: controls.narrowed[chosen.id] ?? chosen.widthM, kind: chosen.kind }, data.assumptions)}
        stations={stations}
        compliance={params?.compliance ?? 0}
      />
    ) : undefined;

  return (
    <section id="simulation" className="flex scroll-mt-[84px] flex-col gap-5">
      {/* 공연 */}
      <Card className="flex flex-wrap items-center justify-between gap-4 px-6 py-4">
        <div className="flex min-w-0 flex-col gap-0.5">
          <span className="break-words text-[20px] leading-7 font-bold text-ink">{event ? `${event.venueName} · ${event.name}` : '공연을 선택하세요'}</span>
          {event && end && (
            <span className="flex flex-wrap items-center gap-x-2 text-[13px] leading-5 text-ink-2">
              {formatDay(event.startAt)} {formatClock(event.startAt)} 시작 · {end.entered ? '종료' : '종료 예상'} {formatClock(end.expectedAt)}
              {end.window && <span>{endWindowText(end.window)}</span>}
              <Badge kind={end.entered ? '입력' : '예측'} />
            </span>
          )}
        </div>
        <Button variant="primary" icon="search" onClick={() => setPicking(true)} disabled={!data}>
          공연 선택
        </Button>
      </Card>
      {event && <AudienceTags key={event.programmeId ?? event.id} programme={event.programmeId}
        initial={prediction?.brief?.labelled ? prediction.brief.audienceType : null} />}
      <EventPicker open={picking} onClose={() => setPicking(false)} />

      <div className="grid grid-cols-[minmax(0,1fr)_360px] items-start gap-5">
        {/* 지도 */}
        <Card className="flex min-w-0 flex-col overflow-hidden">
          <ol className="flex flex-wrap items-center gap-x-5 gap-y-1.5 px-5 py-3 text-[13px] text-ink" aria-label="시뮬레이션 절차">
            {STEPS.map((step, i) => (
              <li key={step} className="flex items-center gap-2">
                <span className="flex size-5 items-center justify-center rounded-full bg-accent text-[11px] font-bold text-white" aria-hidden="true">
                  {i + 1}
                </span>
                {step}
              </li>
            ))}
          </ol>

          <div className="relative h-[640px] border-t border-line bg-map-ground">
            {!event || !prediction ? (
              <Centered>보행 흐름을 확인할 공연을 선택하세요.</Centered>
            ) : !graph || !data ? (
              <Centered>보행로를 불러오는 중입니다…</Centered>
            ) : !run ? (
              <Centered>공연장 출구 위치 자료가 없어 경로를 산출할 수 없습니다.</Centered>
            ) : unsupported ? (
              <Centered>지도 표시를 지원하지 않는 브라우저입니다. 구간별 결과는 하단 표에서 확인하세요.</Centered>
            ) : (
              <Suspense fallback={<Centered>지도를 불러오는 중입니다…</Centered>}>
                <WalkMap
                  graph={graph}
                  result={run.current}
                  minute={minute}
                  controls={controls}
                  selected={selected}
                  pickAt={pickAt}
                  popup={popup}
                  focus={focus}
                  venueName={event.venueName}
                  venueExitIds={run.demand.exits.map((x) => x.id)}
                  onSelect={(id, at) => walk().select(id, at)}
                  onBasemap={setBasemap}
                  onUnsupported={() => setUnsupported(true)}
                />
              </Suspense>
            )}
            {run && (
              <span className="pointer-events-none absolute top-4 left-4 inline-flex h-8 items-center gap-2 rounded-lg border border-line bg-card/95 px-3 text-[13px] font-medium text-ink shadow-pop">
                <span className="size-2 rounded-full bg-accent" aria-hidden="true" />
                {viewing}
                {edited && <span className="text-ink-2">· 수정 사항 {placed.length}건 반영</span>}
              </span>
            )}
            {run && basemap === 'failed' && (
              <span className="pointer-events-none absolute top-14 left-4 rounded-lg border border-line bg-card/95 px-2.5 py-1 text-[12px] text-ink-2">배경 지도 로드 실패 · 보행로만 표시</span>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-line px-5 py-3 text-[12px] text-ink-2">
            <span className="flex items-center gap-1.5">
              <span className="h-[5px] w-5 rounded-full" style={{ background: RAMP_CSS }} aria-hidden="true" />
              통행량
            </span>
            <span className="flex items-center gap-2.5">
              혼잡 구간 테두리
              {HALO.map((h) => (
                <span key={h.status} className="flex items-center gap-1">
                  <span className={`flex h-[11px] w-6 items-center rounded-full px-[3px] ${h.tone}`} aria-hidden="true">
                    <span className="h-[5px] w-full rounded-full bg-[#278452] shadow-[0_0_0_1px_#fff]" />
                  </span>
                  {h.status}
                </span>
              ))}
            </span>
            <span className="ml-auto flex items-center gap-3">
              {(['block', 'narrow', 'staff'] as const).map((k) => (
                <span key={k} className="flex items-center gap-1">
                  <Icon name={CONTROL_ICON[k]} size={13} />
                  {CONTROL_NAME[k]}
                </span>
              ))}
            </span>
          </div>

          <div className="flex items-center gap-3 border-t border-line px-5 py-3 text-[13px]">
            <div className="flex shrink-0 rounded-lg bg-page p-[3px]" role="group" aria-label="조회 범위">
              <button
                type="button"
                aria-pressed={minute === null}
                onClick={() => {
                  walk().setPlaying(false);
                  walk().setMinute(null);
                }}
                className={`rounded-md border px-3 py-1.5 ${minute === null ? 'border-line bg-card font-bold text-ink' : 'border-transparent text-ink-2 hover:text-ink'}`}
              >
                전체 시간대
              </button>
              <button
                type="button"
                aria-pressed={minute !== null}
                onClick={() => walk().setMinute(minute ?? Math.max(1, now?.peakQueueTotalMinute ?? 15))}
                className={`rounded-md border px-3 py-1.5 ${minute !== null ? 'border-line bg-card font-bold text-ink' : 'border-transparent text-ink-2 hover:text-ink'}`}
              >
                시간별
              </button>
            </div>
            <Button className="h-9 px-3" icon={playing ? 'pause' : 'play'} onClick={() => walk().setPlaying(!playing)} disabled={!run}>
              {playing ? '일시정지' : '재생'}
            </Button>
            <label htmlFor="walk-minute" className="sr-only">
              조회 시점
            </label>
            <input
              id="walk-minute"
              type="range"
              className="olp-range"
              min={0}
              max={LAST_MINUTE}
              step={1}
              value={minute ?? 0}
              disabled={!run}
              aria-valuetext={viewing}
              onChange={(e) => {
                walk().setPlaying(false);
                walk().setMinute(Number(e.target.value));
              }}
              style={{ ['--fill' as string]: `${((minute ?? 0) / LAST_MINUTE) * 100}%` }}
            />
            <output htmlFor="walk-minute" className="shrink-0 text-right font-medium whitespace-nowrap text-ink">
              {run && t !== null ? `${viewing} · 대기 ${formatNumber(t >= 0 ? (run.current.queueTotal[t] ?? 0) : 0)}명` : viewing}
            </output>
          </div>
        </Card>

        {/* 결과와 놓은 것 */}
        <div className="flex flex-col gap-5">
          <Card className="flex flex-col gap-4 p-5">
            <CardHeader title="시뮬레이션 결과" />
            {!now || !before ? (
              <p className="text-[13px] text-ink-2">분석 결과가 없습니다.</p>
            ) : (
              <div className="grid grid-cols-2 gap-x-4 gap-y-4">
                <Stat
                  label="최대 대기 인원"
                  value={`${formatNumber(now.peakQueueTotal)}명`}
                  {...(edited ? delta(now.peakQueueTotal, before.peakQueueTotal, '명') : { note: now.peakQueueTotal >= 0.5 ? clockText(end, now.peakQueueTotalMinute) : '대기 없음' })}
                />
                <Stat
                  label="대기열 해소 시각"
                  value={now.clearMinute === null ? '예측 범위 초과' : now.peakQueueTotal < 0.5 ? '–' : clockText(end, now.clearMinute, false)}
                  {...(edited && now.clearMinute !== null && before.clearMinute !== null ? delta(now.clearMinute, before.clearMinute, '분') : { note: now.clearMinute ? `종료 +${now.clearMinute}분` : undefined })}
                />
                <Stat label="1인당 평균 대기 시간" value={`${formatNumber(waitEach(now), 1)}분`} {...(edited ? delta(waitEach(now), waitEach(before), '분', 1) : { note: '분석 대상 인원 기준' })} />
                <Stat label="1인당 평균 보행 시간" value={`${formatNumber(now.meanWalkMinutes, 1)}분`} {...(edited ? delta(now.meanWalkMinutes, before.meanWalkMinutes, '분', 1) : { note: '대기 시간 제외' })} />
              </div>
            )}
            {now && now.guided >= 0.5 && (
              <p className="rounded-lg bg-accent-tint px-3 py-2 text-[13px] leading-5 text-ink">
                우회 안내 수용 인원 {formatNumber(now.guided)}명 · 평균 추가 보행 시간 {formatNumber(now.detourMinutesTotal / now.guided, 1)}분
              </p>
            )}
            {now && now.stranded >= 0.5 && (
              <p className="rounded-lg bg-bottleneck-tint px-3 py-2 text-[13px] leading-5 text-ink">통행 통제로 {formatNumber(now.stranded)}명의 이동 경로가 단절됐습니다. 통제 구간을 확인하세요.</p>
            )}
          </Card>

          <Card className="flex flex-col gap-3 p-5">
            <CardHeader
              title="수정 사항"
              actions={
                <Button variant="ghost" className="h-8 px-2" icon="history" onClick={() => walk().undo()} disabled={!history.length}>
                  되돌리기
                </Button>
              }
            />
            {!placed.length ? (
              <p className="text-[13px] leading-5 text-ink-2">적용된 수정 사항이 없습니다.</p>
            ) : (
              <ul className="flex flex-col gap-2">
                {placed.map((x) => {
                  const e = edgeById.get(x.id)!;
                  return (
                    <li key={x.id} className={`flex items-center justify-between gap-2 rounded-[10px] border px-3 py-2 ${selected === x.id ? 'border-accent bg-accent-tint' : 'border-line'}`}>
                      <button type="button" onClick={() => focusOn(x.id)} className="flex min-w-0 flex-1 items-center gap-2.5 text-left" title="지도에서 수정">
                        <span className="text-ink-2">
                          <Icon name={CONTROL_ICON[x.kind]} size={16} />
                        </span>
                        <span className="flex min-w-0 flex-col">
                          <span className="text-[13px] leading-[18px] font-bold text-ink">
                            {CONTROL_NAME[x.kind]} <span className="font-normal text-ink-2">· {detailOf(x)}</span>
                          </span>
                          <span className="truncate text-[12px] leading-[17px] text-ink-2">
                            {whereOf(x.id)} · {KIND_NAME[e.kind]}
                          </span>
                        </span>
                      </button>
                      <button type="button" onClick={() => walk().remove(x.id)} aria-label={`${CONTROL_NAME[x.kind]} 해제`} title="해제" className="flex size-8 shrink-0 items-center justify-center rounded-lg text-ink-2 hover:bg-subtle hover:text-ink">
                        <Icon name="x" size={16} />
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
            {placed.length > 1 && (
              <button type="button" onClick={() => walk().clear()} className="self-start text-[13px] font-medium text-ink-2 underline decoration-line-strong hover:text-ink">
                전체 해제
              </button>
            )}
            {controls.staffed.length > 0 && data && (
              <p className="text-[12px] leading-[18px] text-ink-2">
                안전요원 {controls.staffed.length}개소 · 개소당 {data.assumptions.staffPerJunction}명 기준 총 {controls.staffed.length * data.assumptions.staffPerJunction}명 (가정)
              </p>
            )}
          </Card>

          <Card id="simulation-settings" tabIndex={-1} aria-label="시뮬레이션 조건" className="flex scroll-mt-[84px] flex-col gap-4 p-5">
            <CardHeader title="시뮬레이션 조건" description="변경하면 즉시 반영됩니다" />
            {params && prediction ? (
              <>
                <Slider
                  label="관객 수"
                  value={params.attendance}
                  {...PARAM_LIMITS.attendance}
                  onChange={(v) => setParam('attendance', v)}
                  valueText={`${formatNumber(params.attendance)}명`}
                  minLabel={`${formatNumber(PARAM_LIMITS.attendance.min)}명`}
                  maxLabel={`${formatNumber(PARAM_LIMITS.attendance.max)}명`}
                />
                <Slider
                  label="지하철 이용 비율"
                  value={Math.min(params.stationShare, Math.max(0.1, 1 - params.vehicleRatio))}
                  min={PARAM_LIMITS.stationShare.min}
                  max={maxStationShare}
                  step={PARAM_LIMITS.stationShare.step}
                  onChange={(v) => setParam('stationShare', v)}
                  valueText={`${formatNumber(params.stationShare * 100)}%`}
                  minLabel="10%"
                  maxLabel={`${formatNumber(maxStationShare * 100)}%`}
                />
                <Slider
                  label="안전요원 안내 수용률"
                  badge={<Badge kind="가정" />}
                  value={params.compliance}
                  {...PARAM_LIMITS.compliance}
                  onChange={(v) => setParam('compliance', v)}
                  valueText={`${formatNumber(params.compliance * 100)}%`}
                  minLabel="0%"
                  maxLabel="100%"
                />
              </>
            ) : (
              <p className="text-[13px] text-ink-2">조건을 설정할 공연을 선택하세요.</p>
            )}
          </Card>

          {run && (
            <Card className="flex flex-col gap-2.5 p-5">
              <CardHeader title="인원 정보" />
              <p className="text-[13px] leading-5 text-ink">
                분석 대상 · {(['rail', 'bus', 'car'] as const)
                  .filter((m) => run.demand.onMap[m] >= 0.5)
                  .map((m) => `${MODE_NAME[m]} ${formatNumber(run.demand.onMap[m])}명`)
                  .join(' · ') || '0명'}
              </p>
              {run.demand.offMap.length > 0 && (
                <details className="text-[12px] leading-[18px] text-ink-2">
                  <summary className="cursor-pointer">
                    경로 분석 제외 {formatNumber(run.demand.offMap.reduce((sum, o) => sum + o.people, 0))}명
                  </summary>
                  <dl className="mt-2 space-y-1.5">
                    {(['no-place', 'not-on-map'] as const).map((reason) => {
                      const entries = run.demand.offMap.filter((o) => o.reason === reason);
                      return entries.length > 0 && (
                        <div key={reason}>
                          <dt className="font-medium text-ink">{reason === 'no-place' ? '목적지 미확정' : '목적지 위치 자료 없음'}</dt>
                          <dd>{entries.map((o) => `${MODE_NAME[o.mode]} ${formatNumber(o.people)}명`).join(' · ')}</dd>
                        </div>
                      );
                    })}
                  </dl>
                </details>
              )}
              {run.demand.assumedExits && <p className="text-[12px] leading-[18px] text-ink-2">출구 위치 미확인 · 공연장과 가장 가까운 보행로를 출발점으로 가정합니다.</p>}
            </Card>
          )}
        </div>
      </div>

      {/* 붐비는 길과 대기 추이 */}
      <div className="grid grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)] gap-5">
        <Card className="flex flex-col overflow-hidden">
          <CardHeader className="px-6 pt-5 pb-4" title="혼잡 구간" description="전체 시간대 최고 혼잡도 기준" />
          {!run ? (
            <p className="px-6 pb-6 text-[13px] text-ink-2">분석할 공연을 선택하세요.</p>
          ) : !busy.length ? (
            <p className="px-6 pb-6 text-[13px] text-ink-2">붐비는 길 없음</p>
          ) : (
            <table className="w-full text-[13px]">
              <thead>
                <tr className="border-y border-line bg-subtle text-[12px] whitespace-nowrap text-ink-2">
                  <th scope="col" className="px-6 py-2.5 text-left font-medium">구간</th>
                  <th scope="col" className="px-3 py-2.5 text-left font-medium">혼잡도</th>
                  <th scope="col" className="px-3 py-2.5 text-right font-medium">최대 유입량</th>
                  <th scope="col" className="px-3 py-2.5 text-right font-medium">최대 대기 인원</th>
                  <th scope="col" className="px-6 py-2.5 text-right font-medium">유효 폭</th>
                </tr>
              </thead>
              <tbody>
                {busy.map((b) => (
                  <tr key={b.edge.id} onClick={() => focusOn(b.edge.id)} className={`cursor-pointer border-b border-line last:border-b-0 hover:bg-subtle ${selected === b.edge.id ? 'bg-subtle' : ''}`}>
                    <th scope="row" className="px-6 py-2.5 text-left font-normal">
                      <button type="button" className="text-left font-medium text-ink" onClick={() => focusOn(b.edge.id)}>
                        {b.where}
                      </button>
                      <span className="block text-[12px] text-ink-2">{KIND_NAME[b.edge.kind]}</span>
                    </th>
                    <td className="px-3 py-2.5 whitespace-nowrap">
                      <StatusLabel status={b.series.worst} />
                    </td>
                    <td className="px-3 py-2.5 text-right">
                      {formatNumber(b.peakInflow)}명/분
                      <span className="block text-[12px] text-ink-2">
                        용량 {formatNumber(b.series.capacityPerMin)}명/분 · {clockText(end, b.peakMinute, false)}
                      </span>
                    </td>
                    <td className="px-3 py-2.5 text-right font-bold">{formatNumber(b.series.peakQueue)}명</td>
                    <td className="px-6 py-2.5 text-right">
                      {formatNumber(controls.narrowed[b.edge.id] ?? b.edge.widthM, 1)}m
                      <span className="block text-[12px] text-ink-2">{controls.narrowed[b.edge.id] !== undefined ? '장애물' : b.edge.widthSource}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <p className="mt-auto border-t border-line px-6 py-3 text-[12px] leading-[18px] text-ink-2">
            보행로 폭은 대부분 유형별 가정값이며, 실제 대기 인원과 차이가 있을 수 있습니다.
          </p>
        </Card>

        <Card className="flex flex-col gap-3 p-6 pt-5">
          <CardHeader title="시간대별 대기 인원" description="보행로와 목적지 입구의 대기 인원 합계" />
          {!run ? (
            <p className="text-[13px] text-ink-2">분석할 공연을 선택하세요.</p>
          ) : (
            <div className="h-[250px]" role="img" aria-label={`시간대별 대기 인원. 최대 ${formatNumber(run.current.totals.peakQueueTotal)}명`}>
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart data={chart} margin={{ top: 10, right: 8, bottom: 0, left: 0 }}>
                  <CartesianGrid vertical={false} stroke="var(--color-line)" />
                  <XAxis
                    dataKey="x"
                    type="number"
                    domain={[0, LAST_MINUTE]}
                    ticks={[0, 15, 30, 45, 60, 75, 90]}
                    tickFormatter={(v) => (Number.isFinite(endMs) ? formatClock(endMs + Number(v) * MINUTE_MS) : `+${v}분`)}
                    tick={{ fontSize: 11, fill: 'var(--color-ink-2)' }}
                    axisLine={{ stroke: 'var(--color-line)' }}
                    tickLine={false}
                  />
                  <YAxis width={44} tickFormatter={(v) => formatNumber(Number(v))} tick={{ fontSize: 11, fill: 'var(--color-ink-2)' }} axisLine={false} tickLine={false} allowDecimals={false} />
                  <Tooltip
                    formatter={(v, name) => [`${formatNumber(Number(v))}명`, name === 'before' ? '변경 전' : edited ? '변경 후' : '대기 인원']}
                    labelFormatter={(x) => `${Number.isFinite(endMs) ? formatClock(endMs + Number(x) * MINUTE_MS) : ''} · 종료 +${x}분`}
                    contentStyle={{ borderRadius: 8, border: '1px solid var(--color-line)', fontSize: 12 }}
                  />
                  <Area dataKey="now" stroke="var(--color-accent)" strokeWidth={2.5} fill="var(--color-accent)" fillOpacity={0.1} isAnimationActive={false} />
                  {edited && <Line dataKey="before" stroke="#9AA29D" strokeWidth={2} strokeDasharray="5 5" dot={false} isAnimationActive={false} />}
                  {minute !== null && <ReferenceLine x={minute} stroke="var(--color-ink)" strokeDasharray="3 3" />}
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          )}
          {run && (
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px] text-ink-2">
              <span className="flex items-center gap-1.5">
                <svg width="20" height="4" aria-hidden="true">
                  <path d="M1 2h18" stroke="var(--color-accent)" strokeWidth="2.5" strokeLinecap="round" />
                </svg>
                {edited ? '변경 후' : '대기 인원'}
              </span>
              {edited && (
                <span className="flex items-center gap-1.5">
                  <svg width="20" height="4" aria-hidden="true">
                    <path d="M1 2h18" stroke="#9AA29D" strokeWidth="2.5" strokeDasharray="3 3" strokeLinecap="round" />
                  </svg>
                  변경 전
                </span>
              )}
              {end?.window && <span>예상 종료 {formatClock(end.expectedAt)} 기준 · 종료 시각에 따라 변동</span>}
            </div>
          )}
        </Card>
      </div>
    </section>
  );
}

function Centered({ children }: { children: string }) {
  return <div className="flex h-full items-center justify-center px-6 text-center text-[13px] text-ink-2">{children}</div>;
}
