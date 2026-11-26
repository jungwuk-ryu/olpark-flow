import { describe, expect, it } from 'vitest';
import sample from '../api/testing/server-bundle.sample.json';
import { fromServer, type ServerBundle } from '../api';
import { formatClock } from '../lib/format';
import {
  arrivalQuantile,
  busiestHours,
  checklist,
  eveningHours,
  hoursText,
  rankWords,
  retimedStationHours,
  staffingWindow,
  stationRiders,
  stationTimes,
} from './brief';
import { endTimeOf } from './scenario';
import type { EventBrief } from '../data/contract';

/** 서버 응답 예시: 11/27(금) KSPO DOME 19시 공연과 같은 날 올림픽홀 공연 */
const data = fromServer(sample as unknown as ServerBundle);
const dome = data.events.events.find((e) => e.id === 'kspo-20261127-1900')!;
const domeP = data.predictions.predictions.find((p) => p.eventId === dome.id)!;
const hall = data.events.events.find((e) => e.id === 'olympichall-20261127-2000')!;
const hallP = data.predictions.predictions.find((p) => p.eventId === hall.id)!;
const friday = data.evenings!.find((e) => e.date === '2026-11-27')!;
const clock = (t: { at: Date; earliest: Date; latest: Date }) => [formatClock(t.earliest), formatClock(t.at), formatClock(t.latest)];

/** 종료 10분 뒤부터 5분마다 40% · 30% · 20% · 10% */
const simple: NonNullable<EventBrief['arrival']> = {
  kind: 'single_show',
  binMinutes: 5,
  onsetMinutes: 10,
  bins: [
    [10, 0.4],
    [15, 0.3],
    [20, 0.2],
    [25, 0.1],
  ],
};

describe('역 도착 곡선 읽기', () => {
  it('누적 비율에 닿는 시점을 구간 안에서 고르게 나눠 낸다', () => {
    expect(arrivalQuantile(simple, 0.4)).toBeCloseTo(15, 9);
    // 절반: 두 번째 구간(15~20분)의 3분의 1 지점
    expect(arrivalQuantile(simple, 0.5)).toBeCloseTo(15 + (0.1 / 0.3) * 5, 9);
    expect(arrivalQuantile(simple, 0.8)).toBeCloseTo(15 + 5 + (0.1 / 0.2) * 5, 9);
    expect(arrivalQuantile(simple, 1)).toBeCloseTo(30, 9);
    expect(arrivalQuantile(simple, 0)).toBeCloseTo(10, 9);
  });

  it('비율 합이 1이 아니어도 그 합을 전체로 본다', () => {
    const cut = { ...simple, bins: simple.bins.slice(0, 2) as [number, number][] };
    expect(arrivalQuantile(cut, 1)).toBeCloseTo(20, 9);
    expect(arrivalQuantile({ ...simple, bins: [] }, 0.5)).toBe(10);
  });
});

describe('역이 붐비는 시간', () => {
  const end = endTimeOf(dome, domeP);
  const times = stationTimes(domeP.brief!.arrival!, dome.endAt, end);

  it('KSPO DOME: 종료 예상 21:08, 10분 뒤 역 승차가 늘고 80%가 타는 데 한 시간쯤 걸린다', () => {
    // 한 저녁의 곡선: 종료 35분 뒤 절반, 67분 뒤 80%
    expect(formatClock(times.start.at)).toBe('21:18');
    expect(formatClock(times.half.at)).toBe('21:43');
    expect(formatClock(times.most.at)).toBe('22:15');
    expect(times.ranged).toBe(true);
  });

  it('종료 시각 범위(±23분)만큼 앞뒤로 움직인다', () => {
    expect(clock(times.start)).toEqual(['20:55', '21:18', '21:41']);
    expect(clock(times.most)).toEqual(['21:52', '22:15', '22:38']);
  });

  it('배치 시간은 가장 이른 종료부터 가장 늦은 80% 탑승까지다', () => {
    const w = staffingWindow(end, times);
    expect([formatClock(w.from), formatClock(w.until)]).toEqual(['20:45', '22:38']);
  });

  it('실제 종료 시각을 넣으면 그 시각에서 재고 범위가 없어진다', () => {
    const entered = endTimeOf(dome, domeP, '2026-11-27T21:30:00+09:00');
    const t = stationTimes(domeP.brief!.arrival!, dome.endAt, entered);
    expect(clock(t.start)).toEqual(['21:40', '21:40', '21:40']);
    expect(formatClock(t.most.at)).toBe('22:37');
    expect(t.ranged).toBe(false);
    const w = staffingWindow(entered, t);
    expect([formatClock(w.from), formatClock(w.until)]).toEqual(['21:30', '22:37']);
  });

  it('평균 곡선(올림픽홀)은 서버가 낸 종료 시각에 고정하고 범위를 더하지 않는다', () => {
    const arrival = hallP.brief!.arrival!;
    expect(arrival.kind).toBe('average');
    const moved = stationTimes(arrival, hall.endAt, endTimeOf(hall, hallP, '2026-11-27T23:00:00+09:00'));
    const kept = stationTimes(arrival, hall.endAt, endTimeOf(hall, hallP));
    expect(moved.start.at).toEqual(kept.start.at);
    expect(kept.ranged).toBe(false);
    expect(kept.most.earliest).toEqual(kept.most.latest);
  });
});

describe('시간대별 역 승차', () => {
  it('가장 붐비는 시간은 퇴장 시간대에서 최댓값의 80% 이상인 시다', () => {
    expect(busiestHours(friday.hours)).toEqual([21, 22]);
    expect(busiestHours([{ hour: 20, expected: 500, window: false }])).toEqual([]);
    expect(hoursText([21, 22])).toBe('21~22시대');
    expect(hoursText([21])).toBe('21시대');
    expect(hoursText([19, 22])).toBe('19·22시대');
    expect(hoursText([])).toBe('–');
  });

  it('지난 1년 공연일 가운데 자리를 말로 옮긴다', () => {
    expect([rankWords(15.9), rankWords(25), rankWords(60), rankWords(80), rankWords(95)]).toEqual(['작은 편', '보통', '보통', '큰 편', '매우 큰 편']);
  });

  it('올림픽공원역으로 가는 인원은 두 호선의 합이다', () => {
    const lines = domeP.brief!.stationLines!;
    expect(stationRiders(domeP.brief!)).toBe(lines.filter((l) => l.direction === 'A').reduce((s, l) => s + l.people, 0));
    expect(stationRiders(domeP.brief!)).toBeGreaterThan(0.95 * domeP.brief!.modes!.rail);
  });

  it('입력이 없으면 서버가 준 시간대별 값 그대로다', () => {
    const hours = eveningHours(friday, data.predictions.predictions, {});
    expect(hours.map((h) => h.expected)).toEqual(friday.hours.map((h) => h.expected));
    expect(hours.every((h) => !h.retimed)).toBe(true);
  });

  it('실제 종료 시각을 넣으면 그 공연의 몫을 한 저녁 곡선으로 다시 나눈다', () => {
    const riders = stationRiders(domeP.brief!);
    const again = retimedStationHours(domeP.brief!, '2026-11-27T21:30:00+09:00', friday.date)!;
    // 21:30에 끝나면 역 승차는 21:37 구간부터라 21시대에는 20여 분어치만 들어간다
    const share21 = domeP.brief!.arrival!.bins.filter(([offset]) => 30 + offset < 60).reduce((s, [, f]) => s + f, 0);
    expect(again[21]).toBeCloseTo(riders * share21, 6);
    expect(Object.values(again).reduce((x, y) => x + y, 0)).toBeLessThanOrEqual(riders);

    const hours = eveningHours(friday, data.predictions.predictions, { [dome.id]: '2026-11-27T21:30:00+09:00' });
    const before = new Map(friday.hours.map((h) => [h.hour, h]));
    const own = domeP.brief!.stationHours!;
    for (const h of hours.filter((x) => x.retimed)) {
      const expected = before.get(h.hour)!.expected - (own[String(h.hour)] ?? 0) + (again[h.hour] ?? 0);
      expect(h.expected).toBe(Math.max(0, Math.round(expected)));
    }
    // 늦게 끝났으니 21시대는 줄고 22시대는 는다
    const at = (hour: number) => hours.find((h) => h.hour === hour)!;
    expect(at(21).expected).toBeLessThan(before.get(21)!.expected);
    expect(at(22).expected).toBeGreaterThan(before.get(22)!.expected);
    expect(at(20).retimed).toBe(false);
  });

  it('평균 곡선뿐인 공연은 다시 나누지 않는다', () => {
    expect(retimedStationHours(hallP.brief!, '2026-11-27T23:00:00+09:00', friday.date)).toBeNull();
    const hours = eveningHours(friday, data.predictions.predictions, { [hall.id]: '2026-11-27T23:00:00+09:00' });
    expect(hours.every((h) => !h.retimed)).toBe(true);
  });
});

describe('실행표', () => {
  const steps = checklist(dome, domeP, endTimeOf(dome, domeP), data.assumptions);

  it('시간순으로 배치·종료·출차·역 승차 단계를 낸다', () => {
    expect(steps.map((s) => s.key)).toEqual(['ready', 'end', 'vehicles', 'station-start', 'station-half', 'station-most']);
    expect(steps.map((s) => formatClock(s.at))).toEqual(['20:45', '21:08', '21:13', '21:18', '21:43', '22:15']);
    for (let i = 1; i < steps.length; i++) expect(steps[i]!.at.getTime()).toBeGreaterThanOrEqual(steps[i - 1]!.at.getTime());
  });

  it('인원은 예측의 수단별 인원 그대로다', () => {
    const m = domeP.brief!.modes!;
    const end = steps.find((s) => s.key === 'end')!;
    expect(end.detail).toContain(`관객 약 ${domeP.attendance!.value.toLocaleString('ko-KR')}명`);
    expect(end.detail).toContain(`지하철 ${m.rail.toLocaleString('ko-KR')}명`);
    expect(end.detail).toContain(`차량 약 ${m.vehicles.toLocaleString('ko-KR')}대`);
    expect(steps.find((s) => s.key === 'station-start')!.detail).toContain(`${stationRiders(domeP.brief!).toLocaleString('ko-KR')}명`);
  });

  it('배치 완료 시각은 하나이고, 나머지는 종료 시각 범위만큼 움직인다', () => {
    expect(steps.find((s) => s.key === 'ready')!.ranged).toBe(false);
    const most = steps.find((s) => s.key === 'station-most')!;
    expect(most.ranged).toBe(true);
    expect([formatClock(most.earliest), formatClock(most.latest)]).toEqual(['21:52', '22:38']);
  });

  it('실제 종료 시각을 넣으면 배치 완료 단계가 빠지고 모든 시각이 하나로 정해진다', () => {
    const entered = checklist(dome, domeP, endTimeOf(dome, domeP, '2026-11-27T21:30:00+09:00'), data.assumptions);
    expect(entered.map((s) => s.key)).toEqual(['end', 'vehicles', 'station-start', 'station-half', 'station-most']);
    expect(entered.every((s) => !s.ranged)).toBe(true);
    expect(entered[0]!.title).toBe('공연 종료 (입력한 시각)');
    expect(formatClock(entered[0]!.at)).toBe('21:30');
  });

  it('역 승차 예측이 없는 공연장은 종료 단계만 낸다', () => {
    const woori = data.events.events.find((e) => e.venueId === 'woori-art-hall')!;
    const p = data.predictions.predictions.find((x) => x.eventId === woori.id)!;
    const only = checklist(woori, p, endTimeOf(woori, p), data.assumptions);
    expect(only.map((s) => s.key)).toEqual(['end']);
    expect(only[0]!.detail).toContain('관객 약');
  });
});
