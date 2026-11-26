import { describe, expect, it } from 'vitest';
import { clockEnvelope, clockTimes, endWindowFrom, FIXED_END, valueAfterEnd } from './endtime';
import { simulate } from './simulate';
import { forkNetwork, tenMinutesFlat, testAssumptions } from './testing/fixtures';

// 종료 후 1~5분 시점의 대기: 3분에 정점 30명
const queue = [10, 20, 30, 15, 0];

describe('종료 후 분 기준 계열 읽기', () => {
  it('종료 전은 0, t번째 값은 종료 후 t+1분 시점이다', () => {
    expect(valueAfterEnd(queue, -4)).toBe(0);
    expect(valueAfterEnd(queue, 0)).toBe(0);
    expect(valueAfterEnd(queue, 1)).toBe(10);
    expect(valueAfterEnd(queue, 3)).toBe(30);
  });

  it('계산 범위를 넘으면 마지막 값을 쓴다 (풀리지 않은 줄은 남는다)', () => {
    expect(valueAfterEnd(queue, 9)).toBe(0);
    expect(valueAfterEnd([10, 20, 7], 9)).toBe(7);
  });
});

describe('종료 시각 범위를 시계 시각 범위로 옮기기', () => {
  it('종료 시각이 확정이면 범위는 계열 그대로다', () => {
    const band = clockEnvelope(queue, FIXED_END, -1, 6);
    expect(band.map((b) => b.x)).toEqual([-1, 0, 1, 2, 3, 4, 5, 6]);
    for (const b of band) {
      expect(b.low).toBe(valueAfterEnd(queue, b.x));
      expect(b.high).toBe(b.low);
    }
  });

  it('2분 일찍~3분 늦게 끝날 수 있으면 정점은 예상 정점 2분 전부터 3분 후까지 어디든 올 수 있다', () => {
    const band = clockEnvelope(queue, { earlyMinutes: -2, lateMinutes: 3 }, -3, 10);
    const at = (x: number) => band.find((b) => b.x === x)!;
    // 예상 종료에 끝나면 정점은 +3분. 범위 안에서 정점값 30명이 닿는 시각은 +1분 ~ +6분
    expect(band.filter((b) => b.high === 30).map((b) => b.x)).toEqual([1, 2, 3, 4, 5, 6]);
    // 가장 일찍 끝나면 종료 2분 전부터 줄이 생긴다
    expect(at(-2).high).toBe(0);
    expect(at(-1).high).toBe(10);
    // 가장 늦게 끝나도 +8분(3분 늦은 종료의 5분째)에는 줄이 없다
    expect(at(7).high).toBe(15);
    expect(at(8).high).toBe(0);
    // 그 시각에 줄이 아직 없거나 이미 풀렸을 수 있으면 아래 끝은 0이다
    expect(at(3).low).toBe(0);
  });

  it('범위의 최댓값은 계열의 최댓값과 같다 (종료 시각은 크기를 바꾸지 않는다)', () => {
    const { network, routes } = forkNetwork();
    const r = simulate({
      network,
      routes,
      assumptions: testAssumptions,
      attendance: 4000,
      egress: tenMinutesFlat,
      directionShare: { A: 0.75, B: 0.25 },
      horizonMinutes: 30,
    });
    const band = clockEnvelope(r.queueTotal, { earlyMinutes: -23, lateMinutes: 23 }, -23, 53);
    expect(Math.max(...band.map((b) => b.high))).toBeCloseTo(r.totals.peakQueueTotal, 9);
    // 정점(종료 후 10분)이 닿을 수 있는 시각 폭 = 종료 시각 범위의 폭
    const peakAt = band.filter((b) => Math.abs(b.high - r.totals.peakQueueTotal) < 1e-9).map((b) => b.x);
    expect(peakAt[0]).toBe(r.totals.peakQueueTotalMinute - 23);
    expect(peakAt[peakAt.length - 1]).toBe(r.totals.peakQueueTotalMinute + 23);
  });
});

describe('시계 시각', () => {
  const end = '2026-11-27T21:08:00+09:00';

  it('종료 후 10분은 예상 종료 기준 21:18, 범위는 20:55~21:41', () => {
    const t = clockTimes(end, 10, { earlyMinutes: -23, lateMinutes: 23 });
    expect(t.at.toISOString()).toBe('2026-11-27T12:18:00.000Z');
    expect(t.earliest.toISOString()).toBe('2026-11-27T11:55:00.000Z');
    expect(t.latest.toISOString()).toBe('2026-11-27T12:41:00.000Z');
  });

  it('범위가 없으면 세 시각이 같다', () => {
    const t = clockTimes(end, 10);
    expect(t.earliest).toEqual(t.at);
    expect(t.latest).toEqual(t.at);
  });

  it('이른·늦은 종료 시각에서 범위를 만들고 분 단위로 바깥쪽으로 올림한다', () => {
    expect(endWindowFrom(end, '2026-11-27T20:45:06+09:00', '2026-11-27T21:30:54+09:00')).toEqual({ earlyMinutes: -23, lateMinutes: 23 });
    expect(endWindowFrom(end, end, end)).toEqual(FIXED_END);
    expect(() => endWindowFrom(end, '모름', end)).toThrow();
  });
});
