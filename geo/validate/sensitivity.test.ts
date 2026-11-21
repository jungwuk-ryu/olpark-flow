/**
 * 민감도 확인 (팀원 B 검증 ②): 길 폭·가정값을 흔들어도
 * "분산 운영안이 현재 운영안보다 낫다"는 결론이 유지되는지 본다.
 * 실행: pnpm vitest run geo/validate/sensitivity.test.ts
 */
import { describe, expect, it } from 'vitest';
import { bundle } from '../../src/data/index';
import { optimize } from '../../src/engine/optimize';
import type { SimulationInput } from '../../src/engine/types';
import { MAX_DETOUR_MINUTES } from '../../src/model/scenario';

const p = bundle.predictions.predictions.find((x) => x.eventId === 'kspo-20261121-a')!;
const base = (): SimulationInput => ({
  network: structuredClone(bundle.network),
  routes: bundle.routes,
  assumptions: structuredClone(bundle.assumptions),
  attendance: p.attendance!.value,
  egress: { kind: 'gamma', totalMinutes: p.egressCurve!.totalMinutes, peakMinute: p.egressCurve!.peakMinute },
  directionShare: p.directionShare!,
  compliance: 0.6,
  horizonMinutes: 90,
  vehicle: { parking: bundle.parking, ratio: p.vehicle!.ratio },
});
const scale = (k: number, filter: (t: string) => boolean = () => true) => (i: SimulationInput) => {
  for (const e of i.network.edges) if (filter(e.type)) e.widthM = Math.round(e.widthM * k * 100) / 100;
};
const CASES: [string, (i: SimulationInput) => void][] = [
  ['기준값', () => {}],
  ['모든 폭 -20%', scale(0.8)],
  ['모든 폭 +20%', scale(1.2)],
  ['역 계단 폭 2.5m', (i) => i.network.edges.forEach((e) => e.type === 'stairs' && (e.widthM = 2.5))],
  ['역 계단 폭 4m', (i) => i.network.edges.forEach((e) => e.type === 'stairs' && (e.widthM = 4))],
  ['비 (보정 0.85)', (i) => (i.weather = '우천')],
  ['비 (보정 0.7)', (i) => { i.weather = '우천'; i.assumptions.weatherFactor['우천'] = 0.7; }],
  ['위험 밀도 0.46㎡/인 (이전 값)', (i) => (i.assumptions.jamAreaPerPersonM2 = 0.46)],
  ['관객 +10%', (i) => (i.attendance = Math.round(i.attendance * 1.1))],
  ['안내 반응률 0.4', (i) => (i.compliance = 0.4)],
  ['퇴장 60분 (정점 15분, 지하철 시간대 데이터와 비슷)', (i) => (i.egress = { kind: 'gamma', totalMinutes: 60, peakMinute: 15 })],
  ['방면 비율 A93·B3·C4 (지하철 실측)', (i) => (i.directionShare = { A: 0.93, B: 0.03, C: 0.04 })],
];

describe('민감도: 분산안 우위가 유지되는가', () => {
  const rows: Record<string, string | number>[] = [];
  for (const [name, mut] of CASES) {
    it(name, () => {
      const input = base();
      mut(input);
      const r = optimize(input, { staff: 6, maxDetourPerPersonMinutes: MAX_DETOUR_MINUTES });
      const cur = r.current.totals;
      const best = r.best.result.totals;
      rows.push({
        경우: name,
        현재_최대대기: Math.round(cur.maxQueue),
        분산_최대대기: Math.round(best.maxQueue),
        현재_부담: Math.round(cur.burdenPersonMinutes),
        분산_부담: Math.round(best.burdenPersonMinutes),
        부담감소율: `${Math.round((1 - best.burdenPersonMinutes / Math.max(1, cur.burdenPersonMinutes)) * 100)}%`,
        현재_해소분: cur.clearMinute ?? '90+',
        분산_해소분: best.clearMinute ?? '90+',
      });
      expect(best.burdenPersonMinutes).toBeLessThanOrEqual(cur.burdenPersonMinutes);
    });
  }
  it('표 출력', () => {
    console.table(rows);
  });
});
