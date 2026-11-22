/** 공연 A의 차량 출차 곡선·대기 해소 시각. src/는 수정하지 않는다. */
import { writeFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { bundle } from '../../src/data/index';
import { simulate } from '../../src/engine/simulate';

const p = bundle.predictions.predictions.find((x) => x.eventId === 'kspo-20261121-a')!;
const quantile = (values: number[], q: number) => {
  const target = values.reduce((a, b) => a + b, 0) * q;
  let sum = 0;
  return values.findIndex((v) => (sum += v) >= target);
};

describe('차량 출차 시간 폭 (공연 A)', () => {
  it('차량 수 보존·유한한 출차·계산 범위 내 대기 해소를 확인한다', () => {
    const scenarios = [
      [p.egressCurve!.totalMinutes, p.egressCurve!.peakMinute], [60, 15], [80, 20],
    ].map(([totalMinutes, peakMinute]) => {
      const r = simulate({
        network: bundle.network, routes: bundle.routes, assumptions: bundle.assumptions,
        attendance: p.attendance!.value,
        egress: { kind: 'gamma', totalMinutes: totalMinutes!, peakMinute: peakMinute! },
        directionShare: p.directionShare!, compliance: 0.6, horizonMinutes: 240,
        vehicle: { parking: bundle.parking, ratio: p.vehicle!.ratio },
      });
      const vehicle = r.vehicle!;
      expect(vehicle).not.toBeNull();
      let totalPassed = 0;
      const exits = vehicle.exits.map((s) => {
        const total = s.passed.reduce((a, b) => a + b, 0);
        totalPassed += total;
        expect(s.passed.every((v) => Number.isFinite(v) && v >= 0)).toBe(true);
        expect(total).toBeCloseTo(s.arrivals.reduce((a, b) => a + b, 0), 5);
        expect(s.queue.at(-1)).toBe(0);
        expect(s.clearMinute).not.toBeNull();
        const q10 = quantile(s.passed, 0.1);
        const q50 = quantile(s.passed, 0.5);
        const q90 = quantile(s.passed, 0.9);
        expect(q10).toBeLessThanOrEqual(q50);
        expect(q50).toBeLessThanOrEqual(q90);
        return {
          exitId: s.exitId, vehicles: total, q10, q50, q90, span10to90: q90 - q10,
          clearMinute: s.clearMinute, maxQueue: s.maxQueue, passed: s.passed,
        };
      });
      // 유한한 계산 범위 바깥 감마 곡선 꼬리는 전체의 0.01% 이내여야 한다.
      expect(Math.abs(totalPassed - vehicle.vehicles)).toBeLessThan(vehicle.vehicles * 0.0001);
      console.table(exits.map(({ passed: _, ...summary }) => ({ egressMinutes: totalMinutes, ...summary })));
      return { egressMinutes: totalMinutes, peakMinute, vehicles: vehicle.vehicles, exits };
    });
    if (process.env.VEHICLE_RESULT) {
      writeFileSync(process.env.VEHICLE_RESULT, JSON.stringify({
        source: '예측', event: '공연 A', horizonMinutes: 240,
        timeOrigin: '가정한 공연 종료 후 분 (분 인덱스)', scenarios,
      }, null, 2) + '\n');
    }
  });
});
