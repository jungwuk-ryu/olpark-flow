import { clockTimes, type ClockTimes } from '../engine';
import type { Assumptions, Evening, EveningHour, EventBrief, EventItem, Prediction } from '../data/contract';
import { formatNumber } from '../lib/format';
import type { EndTime } from './scenario';

/**
 * 운영자가 묻는 순서로 예측을 읽는다: 언제 역이 붐비는지, 언제부터 언제까지 사람을 세울지, 시간순으로 무엇을 할지.
 * 숫자는 모두 서버가 준 예측(관객 수, 수단별 인원, 역 도착 곡선, 시간대별 승차)에서 나온다.
 */

type Arrival = NonNullable<EventBrief['arrival']>;
const MINUTE_MS = 60_000;
const KST_MS = 9 * 60 * MINUTE_MS;
/** 역이 붐비는 시간의 끝: 지하철 이용객 가운데 이 비율이 개찰구를 지났을 때 */
export const MOST_SHARE = 0.8;

/** 도착 곡선에서 누적 비율 q에 닿는 시점 (예상 종료 후 분). 한 구간 안에서는 고르게 지난다고 본다 */
export function arrivalQuantile(arrival: Arrival, q: number): number {
  const total = arrival.bins.reduce((s, [, f]) => s + f, 0);
  if (!(total > 0)) return arrival.onsetMinutes;
  const target = Math.min(1, Math.max(0, q)) * total;
  let passed = 0;
  for (const [offset, fraction] of arrival.bins) {
    if (fraction > 0 && passed + fraction >= target) return offset + ((target - passed) / fraction) * arrival.binMinutes;
    passed += fraction;
  }
  return arrival.bins[arrival.bins.length - 1]![0] + arrival.binMinutes;
}

export interface StationTimes {
  /** 역 승차가 늘기 시작하는 시각 */
  start: ClockTimes;
  /** 지하철 이용객의 절반이 개찰구를 지난 시각 */
  half: ClockTimes;
  /** 지하철 이용객의 80%가 개찰구를 지난 시각 */
  most: ClockTimes;
  /** 종료 시각 범위만큼 앞뒤로 움직이는지 (한 저녁의 곡선일 때만) */
  ranged: boolean;
}

/**
 * 역이 붐비는 시간.
 * 한 저녁의 곡선(single_show)은 종료 시각과 함께 움직이므로 end(예측 또는 운영자 입력)에서 잰다.
 * 평균 곡선(average)은 게시된 시작 시각 기준이고 저녁마다 다른 종료 시각이 이미 섞여 있어, 서버가 낸 예상 종료 시각에 고정한다.
 */
export function stationTimes(arrival: Arrival, serverEndAt: string, end: EndTime): StationTimes {
  const moves = arrival.kind === 'single_show';
  const base = moves ? end.expectedAt : serverEndAt;
  const window = moves ? (end.window ?? undefined) : undefined;
  const at = (minutes: number) => clockTimes(base, Math.round(minutes), window);
  return { start: at(arrival.onsetMinutes), half: at(arrivalQuantile(arrival, 0.5)), most: at(arrivalQuantile(arrival, MOST_SHARE)), ranged: !!window };
}

/** 사람을 세워 둘 시간: 가장 이른 종료부터, 가장 늦게 끝났을 때 이용객의 80%가 탈 때까지 */
export function staffingWindow(end: EndTime, times: StationTimes): { from: Date; until: Date } {
  return { from: clockTimes(end.expectedAt, 0, end.window ?? undefined).earliest, until: times.most.latest };
}

/** 지난 1년 공연일 가운데 자리를 말로 */
export function rankWords(percentile: number): '작은 편' | '보통' | '큰 편' | '매우 큰 편' {
  if (percentile < 25) return '작은 편';
  if (percentile < 75) return '보통';
  if (percentile < 90) return '큰 편';
  return '매우 큰 편';
}

/** 퇴장 시간대 가운데 가장 붐비는 시간들: 최댓값의 80% 이상인 시 */
export function busiestHours(hours: Pick<EveningHour, 'hour' | 'expected' | 'window'>[]): number[] {
  const inside = hours.filter((h) => h.window);
  if (!inside.length) return [];
  const top = Math.max(...inside.map((h) => h.expected));
  return inside.filter((h) => h.expected >= top * 0.8).map((h) => h.hour);
}

/** [21, 22] → "21~22시대", [21] → "21시대", [19, 22] → "19·22시대" */
export function hoursText(hours: number[]): string {
  if (!hours.length) return '–';
  const sorted = [...hours].sort((a, b) => a - b);
  const consecutive = sorted.every((h, i) => i === 0 || h === sorted[i - 1]! + 1);
  if (sorted.length === 1) return `${sorted[0]}시대`;
  return consecutive ? `${sorted[0]}~${sorted[sorted.length - 1]}시대` : `${sorted.join('·')}시대`;
}

const kstDate = (ms: number) => new Date(ms + KST_MS).toISOString().slice(0, 10);
const kstHour = (ms: number) => new Date(ms + KST_MS).getUTCHours();

/** 올림픽공원역으로 가는 지하철 이용객 수 */
export function stationRiders(brief: EventBrief): number {
  return (brief.stationLines ?? []).filter((l) => l.direction === 'A').reduce((s, l) => s + l.people, 0);
}

/**
 * 실제 종료 시각을 넣은 공연이 역의 시간대별 승차에 더하는 인원을 다시 나눈다.
 * 한 저녁의 곡선을 넣은 종료 시각에 맞춰 놓고 그날의 시(時)별로 더한다. 자정을 넘긴 승차는 세지 않는다.
 */
export function retimedStationHours(brief: EventBrief, enteredEndAt: string, date: string): Record<number, number> | null {
  const arrival = brief.arrival;
  if (!arrival || arrival.kind !== 'single_show') return null;
  const riders = stationRiders(brief);
  const end = Date.parse(enteredEndAt);
  const out: Record<number, number> = {};
  for (const [offset, fraction] of arrival.bins) {
    const at = end + offset * MINUTE_MS;
    if (kstDate(at) !== date) continue;
    const hour = kstHour(at);
    out[hour] = (out[hour] ?? 0) + riders * fraction;
  }
  return out;
}

export interface BriefHour extends EveningHour {
  /** 실제 종료 시각으로 다시 나눈 시간. 이 시간의 범위(low·high)는 전날 예측의 것이라 쓰지 않는다 */
  retimed: boolean;
}

/**
 * 그날의 시간대별 역 승차. 실제 종료 시각을 넣은 공연이 있으면 그 공연의 몫을 빼고 다시 나눈 값을 더한다.
 * entered: 공연 ID → 넣은 종료 시각.
 */
export function eveningHours(evening: Evening, predictions: Prediction[], entered: Record<string, string>): BriefHour[] {
  const delta = new Map<number, number>();
  for (const id of evening.eventIds) {
    const at = entered[id];
    const brief = predictions.find((p) => p.eventId === id)?.brief;
    if (!at || !brief?.stationHours) continue;
    const again = retimedStationHours(brief, at, evening.date);
    if (!again) continue;
    for (const [hour, value] of Object.entries(brief.stationHours)) delta.set(Number(hour), (delta.get(Number(hour)) ?? 0) - value);
    for (const [hour, value] of Object.entries(again)) delta.set(Number(hour), (delta.get(Number(hour)) ?? 0) + value);
  }
  return evening.hours.map((h) => {
    const d = delta.get(h.hour);
    if (d === undefined) return { ...h, retimed: false };
    return { ...h, expected: Math.max(0, Math.round(h.expected + d)), crowd: Math.max(0, Math.round(h.crowd + d)), retimed: true };
  });
}

export interface Step {
  key: string;
  /** 예상(또는 입력한) 종료 시각 기준 시각 */
  at: Date;
  earliest: Date;
  latest: Date;
  /** 종료 시각 범위만큼 움직이는 단계인지 */
  ranged: boolean;
  title: string;
  detail: string;
}

/**
 * 시간순 실행표. 예측이 주는 시각과 인원만 적고, 인원 배치 수는 정하지 않는다 (그것을 정할 근거가 없다).
 */
export function checklist(event: EventItem, p: Prediction, end: EndTime, assumptions: Assumptions): Step[] {
  const brief = p.brief;
  const steps: Step[] = [];
  const fixed = (key: string, at: Date, title: string, detail: string) => steps.push({ key, at, earliest: at, latest: at, ranged: false, title, detail });
  const ranged = (key: string, t: ClockTimes, moves: boolean, title: string, detail: string) =>
    steps.push({ key, at: t.at, earliest: t.earliest, latest: t.latest, ranged: moves && t.earliest.getTime() !== t.latest.getTime(), title, detail });

  const finish = clockTimes(end.expectedAt, 0, end.window ?? undefined);
  if (end.window)
    fixed('ready', finish.earliest, '배치 완료', '가장 이른 예상 종료 시각까지 역 방면 보행로와 출입구에 안내 인력을 배치합니다.');

  const modes = brief?.modes;
  const people = p.attendance ? `예상 퇴장 관객 약 ${formatNumber(p.attendance.value)}명.` : '관객 퇴장 시작.';
  const split = modes
    ? ` 지하철 ${formatNumber(modes.rail)}명, 차량 약 ${formatNumber(modes.vehicles)}대(${formatNumber(modes.car)}명), 버스 ${formatNumber(modes.bus)}명, 도보·픽업 등 ${formatNumber(modes.walk + modes.other)}명.`
    : '';
  ranged('end', finish, true, end.entered ? '공연 종료 (입력한 시각)' : '공연 종료 예상', people + split);

  if (modes && modes.vehicles > 0) {
    const lot = clockTimes(end.expectedAt, assumptions.walkToLotMinutes, end.window ?? undefined);
    ranged('vehicles', lot, true, '주차장 출차 시작', `예상 출차량 약 ${formatNumber(modes.vehicles)}대 · 주차장까지 보행 시간 ${formatNumber(assumptions.walkToLotMinutes)}분 가정.`);
  }

  if (brief?.arrival) {
    const times = stationTimes(brief.arrival, event.endAt, end);
    const riders = stationRiders(brief);
    const lines = (brief.stationLines ?? []).filter((l) => l.direction === 'A').map((l) => `${l.name.replace('올림픽공원역 ', '')} ${formatNumber(l.people)}명`);
    ranged('station-start', times.start, times.ranged, '올림픽공원역 승차 증가 시작', `올림픽공원역 예상 이용객 ${formatNumber(riders)}명${lines.length ? ` (${lines.join(', ')})` : ''}.`);
    ranged('station-half', times.half, times.ranged, '누적 승차율 50% 도달', '예측 기준은 역 개찰구 통과 인원입니다.');
    ranged('station-most', times.most, times.ranged, '누적 승차율 80% 도달', '잔여 수요 20%를 고려해 역 방면 안내 인력의 감축 여부를 판단합니다.');
  }
  return steps.sort((a, b) => a.at.getTime() - b.at.getTime());
}
