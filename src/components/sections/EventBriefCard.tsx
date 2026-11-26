import { useEffect, useState, type ReactNode } from 'react';
import { Bar, CartesianGrid, Cell, ComposedChart, ErrorBar, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { Card } from '../ui/Card';
import { Badge, type BadgeKind } from '../ui/Badge';
import { Button } from '../ui/Button';
import { clockTimes } from '../../engine';
import { formatClock, formatClockRange, formatDay, formatNumber, koreanDate } from '../../lib/format';
import { busiestHours, eveningHours, hoursText, staffingWindow, stationRiders, stationTimes } from '../../model/brief';
import { endWindowText } from '../../model/scenario';
import { selectEvent, selectPrediction, useEndTime, useScenario } from '../../store/scenario';
import type { EventBrief } from '../../data/contract';

const AUDIENCE: Record<string, string> = {
  idol: '아이돌 팬층',
  general: '일반 관객',
  senior: '중장년 관객',
  foreign: '해외 아티스트 관객',
  family: '가족 관객',
  musical: '뮤지컬·클래식 관객',
  festival: '페스티벌 관객',
};

const HALF_DAY_MS = 12 * 3_600_000;

/** 입력한 시각(HH:MM)을 예상 종료 시각에 가장 가까운 날짜의 한국 시간으로 */
function enteredAt(time: string, expectedAt: string): string | null {
  if (!/^\d{2}:\d{2}$/.test(time)) return null;
  const expected = Date.parse(expectedAt);
  let at = Date.parse(`${koreanDate(expectedAt)}T${time}:00+09:00`);
  if (!Number.isFinite(at) || !Number.isFinite(expected)) return null;
  // 자정을 넘겨 끝난 공연
  if (at - expected < -HALF_DAY_MS) at += 2 * HALF_DAY_MS;
  if (at - expected > HALF_DAY_MS) at -= 2 * HALF_DAY_MS;
  return `${koreanDate(at)}T${formatClock(at)}:00+09:00`;
}

function Tile({ label, badge, value, lines, last }: { label: string; badge?: BadgeKind; value: ReactNode; lines: ReactNode[]; last?: boolean }) {
  return (
    <div className={`flex flex-col gap-1.5 px-6 py-5 ${last ? '' : 'border-r border-line'}`}>
      <span className="flex items-center gap-1.5 text-[13px] leading-5 font-medium text-ink-2">
        {label}
        {badge && <Badge kind={badge} />}
      </span>
      <span className="text-[28px] leading-[38px] font-bold tracking-[-0.01em] text-ink">{value}</span>
      {lines.map((line, i) => (
        <span key={i} className={`text-[13px] leading-[18px] ${i === 0 ? 'font-medium text-ink' : 'text-ink-2'}`}>
          {line}
        </span>
      ))}
    </div>
  );
}

type ModeKey = 'rail' | 'car' | 'bus' | 'rest';
const MODE_ROWS: { key: ModeKey; label: string; color: string; badge: BadgeKind }[] = [
  { key: 'rail', label: '지하철', color: 'var(--color-accent)', badge: '예측' },
  { key: 'car', label: '차량', color: 'var(--color-vehicle)', badge: '예측' },
  { key: 'bus', label: '버스', color: 'var(--color-route-b)', badge: '예측' },
  { key: 'rest', label: '도보·픽업 등', color: 'var(--color-line-strong)', badge: '가정' },
];

/** 누가 어디로: 첫 귀가 수단별 인원 */
function Modes({ brief, attendance }: { brief: EventBrief; attendance: number }) {
  const m = brief.modes!;
  const people: Record<ModeKey, number> = { rail: m.rail, car: m.car, bus: m.bus, rest: m.walk + m.other };
  const lines = brief.stationLines ?? [];
  const detail: Record<ModeKey, string> = {
    rail: lines.map((l) => `${l.name} ${formatNumber(l.people)}명`).join(' · '),
    car: `약 ${formatNumber(m.vehicles)}대 · 대당 ${formatNumber(m.carOccupancy, 1)}명으로 환산 (가정)`,
    bus: '공원 주변 정류장',
    rest: '잔여 인원에 가정 비율 적용',
  };
  return (
    <div className="flex flex-col gap-3.5">
      <h3 className="text-[15px] leading-[22px] font-bold">귀가 수단별 예상 인원</h3>
      <div className="flex h-3 overflow-hidden rounded-full bg-page" role="img" aria-label="귀가 수단별 인원 비율">
        {MODE_ROWS.map((row) => (
          <span key={row.key} style={{ width: `${(people[row.key] / Math.max(1, attendance)) * 100}%`, background: row.color }} />
        ))}
      </div>
      <ul className="flex flex-col gap-2.5">
        {MODE_ROWS.map((row) => (
          <li key={row.key} className="flex gap-2.5">
            <span className="mt-1.5 size-2.5 shrink-0 rounded-sm" style={{ background: row.color }} aria-hidden="true" />
            <span className="flex min-w-0 flex-1 flex-col">
              <span className="flex items-baseline justify-between gap-3">
                <span className="flex items-center gap-1.5 text-[14px] font-medium text-ink">
                  {row.label}
                  <Badge kind={row.badge} />
                </span>
                <span className="text-[14px] font-bold text-ink">
                  {formatNumber(people[row.key])}명{' '}
                  <span className="text-[12px] font-normal text-ink-2">{formatNumber((people[row.key] / Math.max(1, attendance)) * 100)}%</span>
                </span>
              </span>
              <span className="text-[12px] leading-[18px] text-ink-2">{detail[row.key]}</span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function EventBriefCard() {
  const data = useScenario((s) => s.data);
  const event = useScenario(selectEvent);
  const prediction = useScenario(selectPrediction);
  const entered = useScenario((s) => s.endEntered);
  const setEndEntered = useScenario((s) => s.setEndEntered);
  const end = useEndTime();
  const [time, setTime] = useState('');
  useEffect(() => setTime(''), [event?.id]);

  if (!data || !event || !prediction || !end) {
    return (
      <Card id="brief" className="scroll-mt-[84px] p-6 text-[13px] text-ink-2">
        {data && !data.events.events.length ? '공원 달력에 올라온 공연이 없습니다.' : '공연 브리핑을 불러오는 중입니다…'}
      </Card>
    );
  }

  const brief = prediction.brief ?? null;
  const arrival = brief?.arrival ?? null;
  const times = arrival ? stationTimes(arrival, event.endAt, end) : null;
  const staffing = times ? staffingWindow(end, times) : null;
  const finish = clockTimes(end.expectedAt, 0, end.window ?? undefined);
  const evening = data.evenings?.find((x) => x.eventIds.includes(event.id)) ?? null;
  const hours = evening ? eveningHours(evening, data.predictions.predictions, entered) : [];
  const busiest = busiestHours(hours);
  const retimed = hours.some((h) => h.retimed);
  const others = evening ? evening.eventIds.length - 1 : 0;
  const check = data.evidence?.endTime ?? null;
  const candidate = enteredAt(time, end.expectedAt);
  // 서버에 닿지 않아 목업을 보는 중이면 역 예측이 없는 까닭이 다르다
  const noStation = data.source?.kind === 'server' ? '공연 관객의 승차 수요를 별도로 추정하기 어렵습니다.' : '서버 예측 자료가 필요합니다.';
  const tooEarly = candidate !== null && Date.parse(candidate) <= Date.parse(event.startAt);
  const rows = hours.map((h) => ({
    label: `${h.hour}시`,
    expected: h.expected,
    usual: h.usual,
    window: h.window,
    // 다시 나눈 시간에는 전날 예측의 범위를 그리지 않는다
    error: h.retimed ? undefined : ([h.expected - h.low, h.high - h.expected] as [number, number]),
    low: h.low,
    high: h.high,
    retimed: h.retimed,
  }));

  return (
    <Card id="brief" className="flex scroll-mt-[84px] flex-col overflow-hidden">
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-4 px-6 pt-5 pb-4">
        <div className="flex min-w-0 flex-col gap-1">
          <span className="text-[12px] leading-[18px] text-ink-2">공연 브리핑</span>
          <h2 className="text-[20px] leading-7 font-bold text-ink">
            {event.venueName} · {event.name}
          </h2>
          <p className="text-[13px] leading-5 text-ink-2">
            {formatDay(event.startAt)} {formatClock(event.startAt)} 시작
            {brief ? ` · 관객 유형 · ${AUDIENCE[brief.audienceType] ?? brief.audienceType}${brief.labelled ? '' : ' (미분류 · 일반 관객 기준)'}` : ''}
          </p>
        </div>

        <div className="flex flex-col items-end gap-2">
          <div className="flex items-baseline gap-2 text-[14px]">
            <span className="text-ink-2">{end.entered ? '종료 시각' : '종료 예상'}</span>
            <span className="text-[18px] leading-6 font-bold text-ink">{formatClock(end.expectedAt)}</span>
            {end.window && (
              <span className="text-ink-2" title={`80% 범위 ${formatClockRange(finish.earliest, finish.latest)}`}>
                {endWindowText(end.window)}
              </span>
            )}
            <Badge kind={end.entered ? '입력' : '예측'} />
          </div>
          <form
            className="flex items-center gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (candidate && !tooEarly) setEndEntered(event.id, candidate);
            }}
          >
            <label htmlFor="brief-end" className="text-[13px] text-ink-2">
              실제 종료 시각
            </label>
            <input
              id="brief-end"
              type="time"
              value={time}
              onChange={(e) => setTime(e.target.value)}
              className="h-9 rounded-[var(--radius-control)] border border-line bg-card px-2.5 text-[14px] text-ink"
            />
            <Button type="submit" className="h-9 px-3" disabled={!candidate || tooEarly}>
              적용
            </Button>
            {end.entered && (
              <Button
                variant="ghost"
                className="h-9"
                onClick={() => {
                  setEndEntered(event.id, null);
                  setTime('');
                }}
              >
                예측으로 되돌리기
              </Button>
            )}
          </form>
          <p title={check && !tooEarly ? `KSPO DOME ${check.evenings}일 검증 · ${check.bundle}` : undefined} className="max-w-[440px] text-right text-[12px] leading-[18px] text-ink-2">
            {tooEarly
              ? '종료 시각은 시작 시각 이후여야 합니다.'
              : check
                ? `승차 집중 시간대 적중률 · 전날 예측 ${formatNumber(check.dayBefore.peakHourHitPct)}% / 종료 시각 반영 ${formatNumber(check.endKnown.peakHourHitPct)}%`
                : '실제 종료 시각을 적용하면 운영 일정이 갱신됩니다.'}
          </p>
        </div>
      </div>

      <div className="grid grid-cols-4 border-y border-line">
        <Tile
          label="예상 관객"
          badge="예측"
          value={prediction.attendance ? `${formatNumber(prediction.attendance.value)}명` : '–'}
          lines={
            prediction.attendance
              ? [
                  `범위 ${formatNumber(prediction.attendance.low)}~${formatNumber(prediction.attendance.high)}명`,
                  `좌석 ${formatNumber(event.seatCapacity)}석 · ${data.source?.kind === 'server' ? '같은 공연장의 발권 이력으로 추정' : '시연용 목업 값'}`,
                ]
              : ['관객 수 예측이 없습니다']
          }
        />
        <Tile
          label="지하철 이용"
          badge={prediction.rail ? '예측' : undefined}
          value={prediction.rail ? `${formatNumber(prediction.rail.people)}명` : '–'}
          lines={
            prediction.rail && brief
              ? [
                  `범위 ${formatNumber(prediction.rail.peopleLow)}~${formatNumber(prediction.rail.peopleHigh)}명`,
                  `관객의 ${formatNumber(prediction.rail.ratio * 100)}% · 올림픽공원역 ${formatNumber((stationRiders(brief) / Math.max(1, prediction.rail.people)) * 100)}%`,
                ]
              : [noStation]
          }
        />
        <Tile
          label="역 승차 집중 시간대"
          badge={times ? '예측' : undefined}
          value={times ? formatClockRange(times.start.at, times.most.at) : '–'}
          lines={
            times
              ? [
                  times.ranged ? `종료 시각에 따라 ${formatClockRange(times.start.earliest, times.most.latest)}` : arrival?.kind === 'average' ? '과거 공연일 평균 승차 분포 기준' : '입력한 종료 시각 기준',
                  `누적 승차율 80% 기준 · 50% 도달 ${formatClock(times.half.at)}`,
                ]
              : [noStation]
          }
        />
        <Tile
          last
          label="배치 권장 시간"
          value={staffing ? formatClockRange(staffing.from, staffing.until) : '–'}
          lines={
            staffing
              ? [end.window ? '예상 종료 하한부터 80% 승차 시각 상한까지' : '종료부터 지하철 이용객 80% 탑승까지', '배치 인원은 현장 여건에 따라 결정']
              : [noStation]
          }
        />
      </div>

      <div className="grid grid-cols-[minmax(0,5fr)_minmax(0,7fr)] gap-8 px-6 py-5">
        {brief?.modes && prediction.attendance ? (
          <Modes brief={brief} attendance={prediction.attendance.value} />
        ) : (
          <div className="flex flex-col gap-2">
            <h3 className="text-[15px] leading-[22px] font-bold">귀가 수단별 예상 인원</h3>
            <p className="text-[13px] leading-5 text-ink-2">
              {prediction.vehicle
                ? `차량 이용 비율 ${formatNumber(prediction.vehicle.ratio * 100)}% · 수단별 인원 자료 없음`
                : '이 공연은 수단별 인원 예측이 없습니다.'}
            </p>
          </div>
        )}

        <div className="flex min-w-0 flex-col gap-3">
          <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
            <h3 className="text-[15px] leading-[22px] font-bold">
              {evening ? `${evening.station.name} 시간대별 승차 · ${formatDay(evening.date)}` : '시간대별 역 승차'}
            </h3>
            {evening && busiest.length > 0 && (
              <span className="text-[13px] text-ink-2">
                승차 집중 시간대 <span className="font-bold text-ink">{hoursText(busiest)}</span>
              </span>
            )}
          </div>
          {!evening ? (
            <p className="text-[13px] leading-5 text-ink-2">해당 날짜의 역 승차 예측 자료가 없습니다.</p>
          ) : (
            <>
              <div className="h-[230px]" role="img" aria-label={`${evening.station.name} 시간대별 승차 예측. 승차 집중 시간대 ${hoursText(busiest)}`}>
                <ResponsiveContainer width="100%" height="100%">
                  <ComposedChart data={rows} margin={{ top: 10, right: 8, bottom: 0, left: 0 }}>
                    <CartesianGrid vertical={false} stroke="var(--color-line)" />
                    <XAxis dataKey="label" tick={{ fontSize: 11, fill: 'var(--color-ink-2)' }} axisLine={{ stroke: 'var(--color-line)' }} tickLine={false} />
                    <YAxis width={48} tickFormatter={(v) => formatNumber(Number(v))} tick={{ fontSize: 11, fill: 'var(--color-ink-2)' }} axisLine={false} tickLine={false} />
                    <Tooltip
                      cursor={{ fill: 'var(--color-subtle)' }}
                      content={({ active, payload }) => {
                        const row = active ? (payload?.[0]?.payload as (typeof rows)[number] | undefined) : undefined;
                        if (!row) return null;
                        return (
                          <div className="rounded-lg border border-line bg-card px-3 py-2 text-[12px] leading-[18px] shadow-pop">
                            <div className="font-bold text-ink">{row.label}대</div>
                            <div>예상 {formatNumber(row.expected)}명{row.retimed ? ' (입력한 종료 시각 기준)' : ` · 범위 ${formatNumber(row.low)}~${formatNumber(row.high)}명`}</div>
                            {row.usual !== null && <div className="text-ink-2">공연 없는 날 {formatNumber(row.usual)}명</div>}
                          </div>
                        );
                      }}
                    />
                    <Bar dataKey="expected" radius={[4, 4, 0, 0]} isAnimationActive={false} maxBarSize={44}>
                      {rows.map((row) => (
                        <Cell key={row.label} fill={row.window ? 'var(--color-accent)' : 'var(--color-accent-tint-strong)'} />
                      ))}
                      <ErrorBar dataKey="error" width={6} strokeWidth={1.5} stroke="var(--color-ink)" direction="y" />
                    </Bar>
                    <Line dataKey="usual" stroke="#9AA29D" strokeWidth={2} strokeDasharray="5 5" dot={{ r: 2.5, fill: '#9AA29D', strokeWidth: 0 }} isAnimationActive={false} connectNulls={false} />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px] text-ink-2">
                <span className="flex items-center gap-1.5">
                  <span className="size-2.5 rounded-sm bg-accent" aria-hidden="true" />
                  퇴장 시간대 예상
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="size-2.5 rounded-sm bg-accent-tint-strong" aria-hidden="true" />그 밖의 시간
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="h-3 w-px bg-ink" aria-hidden="true" />
                  80% 범위
                </span>
                {rows.some((r) => r.usual !== null) && (
                  <span className="flex items-center gap-1.5">
                    <svg width="20" height="4" aria-hidden="true">
                      <path d="M1 2h18" stroke="#9AA29D" strokeWidth="2.5" strokeDasharray="3 3" strokeLinecap="round" />
                    </svg>
                    공연 없는 날
                  </span>
                )}
              </div>
              <p className="text-[12px] leading-[18px] text-ink-2">
                5·9호선의 일반 승객과 당일 공연 관객을 합산했습니다{others > 0 ? ` (다른 공연 ${others}건 포함)` : ''}.{' '}
                {retimed
                  ? '종료 시각을 반영해 해당 공연의 시간대별 수요를 재산정했습니다. 재산정 구간은 단일 추정값으로 표시합니다.'
                  : '예측 범위에는 종료 시각의 불확실성이 반영됩니다.'}
                {rows.every((r) => r.usual === null) && ' 비공연일 표본 부족으로 평소 승차 인원을 산출할 수 없습니다.'}
              </p>
            </>
          )}
        </div>
      </div>
    </Card>
  );
}
