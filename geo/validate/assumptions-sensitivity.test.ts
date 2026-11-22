/** A-5: 근거가 부족한 가정 3개를 함께 바꿔 공연 A 결과의 변화를 기록한다. */
import { writeFileSync } from 'node:fs';
import { afterAll, describe, expect, it } from 'vitest';
import { bundle } from '../../src/data/index';
import { optimize } from '../../src/engine/optimize';
import { MAX_DETOUR_MINUTES } from '../../src/model/scenario';

const p = bundle.predictions.predictions.find((x) => x.eventId === 'kspo-20261121-a')!;
const rows: Record<string, unknown>[] = [];

describe('A-5 가정값 27개 조합 민감도', () => {
  for (const crossing of [0.4, 0.6, 0.8]) {
    for (const occupancy of [2.0, 2.4, 3.0]) {
      for (const staffPerJunction of [1, 2, 3]) {
        it(`보행 비율 ${crossing}, 탑승 ${occupancy}명/대, 분기점당 ${staffPerJunction}명`, () => {
          const assumptions = {
            ...structuredClone(bundle.assumptions), crossingPedestrianShare: crossing,
            vehicleOccupancy: occupancy, staffPerJunction,
          };
          const r = optimize({
            network: structuredClone(bundle.network), routes: bundle.routes, assumptions,
            attendance: p.attendance!.value,
            egress: { kind: 'gamma', totalMinutes: p.egressCurve!.totalMinutes, peakMinute: p.egressCurve!.peakMinute },
            directionShare: p.directionShare!, compliance: 0.6, horizonMinutes: 90,
            vehicle: { parking: bundle.parking, ratio: p.vehicle!.ratio },
          }, { staff: 6, maxDetourPerPersonMinutes: MAX_DETOUR_MINUTES });
          const current = r.current.totals;
          const best = r.best.result.totals;
          const used = new Set(r.best.plan.guidance.moves.map((move) => move.junctionNodeId)).size;
          expect(used).toBeLessThanOrEqual(Math.floor(6 / staffPerJunction));
          expect(Number.isFinite(best.burdenPersonMinutes)).toBe(true);
          expect(best.burdenPersonMinutes).toBeLessThanOrEqual(current.burdenPersonMinutes);
          expect(r.current.vehicle!.vehicles).toBeCloseTo(p.attendance!.value * p.vehicle!.ratio / occupancy, 6);
          rows.push({
            crossingPedestrianShare: crossing, vehicleOccupancy: occupancy, staffPerJunction,
            source: '예측', allowedJunctions: Math.floor(6 / staffPerJunction), usedJunctions: used,
            vehicles: r.current.vehicle!.vehicles,
            currentBurden: current.burdenPersonMinutes, bestBurden: best.burdenPersonMinutes,
            reductionPercent: (1 - best.burdenPersonMinutes / current.burdenPersonMinutes) * 100,
            currentMaxQueue: current.maxQueue, bestMaxQueue: best.maxQueue,
            currentVehicleBurden: current.vehicleQueuePersonMinutes,
            bestVehicleBurden: best.vehicleQueuePersonMinutes,
          });
        });
      }
    }
  }
  afterAll(() => {
    if (process.env.ASSUMPTIONS_RESULT) {
      writeFileSync(process.env.ASSUMPTIONS_RESULT, JSON.stringify({
        source: '예측', event: '공연 A', staff: 6, horizonMinutes: 90,
        rangeStatus: '검토용 가정 범위. 신뢰구간·현장 권고값이 아님', rows,
      }, null, 2) + '\n');
    }
    console.log(`A-5 ${rows.length}개 조합, 부담 감소율 ${Math.min(...rows.map((r) => Number(r.reductionPercent))).toFixed(2)}~${Math.max(...rows.map((r) => Number(r.reductionPercent))).toFixed(2)}%`);
  });
});
