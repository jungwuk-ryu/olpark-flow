import { describe, expect, it } from 'vitest';
import { simulate } from './simulate';
import { edge, forkNetwork, node, tenMinutesFlat, testAssumptions } from './testing/fixtures';
import type { SimulationInput } from './types';

function caseOne(extra: Partial<SimulationInput> = {}): SimulationInput {
  const { network, routes } = forkNetwork();
  return {
    network,
    routes,
    assumptions: testAssumptions,
    attendance: 4000, // 10분 × 분당 400명
    egress: tenMinutesFlat,
    directionShare: { A: 0.75, B: 0.25 }, // A 300명/분, B 100명/분
    horizonMinutes: 30,
    ...extra,
  };
}

describe('가상 사례 1: 10분간 분당 400명, 각 병목 분당 200명', () => {
  it('현재 운영안(A 300·B 100)은 A 병목에서 최대 1,000명이 대기한다', () => {
    const r = simulate(caseOne());
    expect(r.edges['e-ja']!.capacityPerMin).toBeCloseTo(200);
    expect(r.totals.maxQueue).toBeCloseTo(1000, 6);
    expect(r.totals.maxQueueEdgeId).toBe('e-ja');
    expect(r.totals.maxQueueMinute).toBe(10);
    expect(Math.max(...r.edges['e-jb']!.queue)).toBe(0);
  });

  it('현재 운영안의 대기시간 합계는 7,500사람·분, 종료 후 15분에 해소된다', () => {
    const r = simulate(caseOne());
    expect(r.totals.queueMinutesTotal).toBeCloseTo(7500, 6);
    expect(r.totals.clearMinute).toBe(15);
  });

  it('분산안(A 240·B 160)은 최대 대기 400명, 대기시간 합계 2,400사람·분', () => {
    const r = simulate(
      caseOne({
        guidance: { startMinute: 0, moves: [{ junctionNodeId: 'J', from: 'A', to: 'B', ratio: 0.2 }] },
        compliance: 1,
      }),
    );
    expect(r.totals.directionShareActual.A).toBeCloseTo(0.6, 9);
    expect(r.totals.directionShareActual.B).toBeCloseTo(0.4, 9);
    expect(r.totals.maxQueue).toBeCloseTo(400, 6);
    expect(r.totals.queueMinutesTotal).toBeCloseTo(2400, 6);
  });

  it('안내 효과는 안내 비율 × 반응률이다 (비율 0.4 · 반응률 0.5 = 0.2)', () => {
    const r = simulate(
      caseOne({
        guidance: { startMinute: 0, moves: [{ junctionNodeId: 'J', from: 'A', to: 'B', ratio: 0.4 }] },
        compliance: 0.5,
      }),
    );
    expect(r.totals.maxQueue).toBeCloseTo(400, 6);
  });
});

describe('인원 보존', () => {
  it('매 분 출구 누적 = 도착 누적 + 이동 중 + 대기 중', () => {
    const { network, routes } = forkNetwork({ lengthA: 420, lengthB: 900, widthA: 1.5, widthB: 1 });
    const r = simulate({
      network,
      routes,
      assumptions: testAssumptions,
      attendance: 7860,
      egress: { kind: 'gamma', totalMinutes: 22, peakMinute: 6 },
      directionShare: { A: 0.72, B: 0.28 },
      guidance: { startMinute: 3, moves: [{ junctionNodeId: 'J', from: 'A', to: 'B', ratio: 0.3 }] },
      compliance: 0.6,
      horizonMinutes: 90,
    });
    const L = r.ledger;
    for (let t = 0; t < r.minutes; t++) {
      expect(L.departed[t]!).toBeCloseTo(L.arrived[t]! + L.inTransit[t]! + L.queued[t]!, 6);
    }
    expect(L.departed[r.minutes - 1]!).toBeCloseTo(7860, 6);
  });
});

describe('막힌 길', () => {
  it('막힌 길로는 0명이 가고, 그 방면 인원은 다른 방면으로 옮겨진다', () => {
    const r = simulate(caseOne({ blockedEdgeIds: ['e-jb'] }));
    expect(r.edges['e-jb']!.arrivals.every((v) => v === 0)).toBe(true);
    expect(r.edges['e-jb']!.passed.every((v) => v === 0)).toBe(true);
    expect(r.unavailableDirections).toEqual(['B']);
    expect(r.totals.directionShareActual.A).toBeCloseTo(1, 9);
    expect(r.warnings.length).toBeGreaterThan(0);
  });

  it('모든 방면이 막히면 오류', () => {
    expect(() => simulate(caseOne({ blockedEdgeIds: ['e-xj'] }))).toThrow();
  });
});

describe('통로 폭 변경 (부스·펜스로 좁아짐)', () => {
  it('A 병목 폭을 2m → 1m로 줄이면 용량이 분당 100명이 되어 최대 대기가 2,000명이 된다', () => {
    const r = simulate(caseOne({ edgeWidthOverrides: { 'e-ja': 1 } }));
    expect(r.edges['e-ja']!.capacityPerMin).toBeCloseTo(100, 9);
    expect(r.totals.maxQueue).toBeCloseTo(2000, 6);
  });
});

describe('줄 번짐', () => {
  it('하류 대기 공간이 차면 상류 통과량을 줄여 상류에 줄이 생긴다', () => {
    // 하류 J→A: 분당 100명, 대기 공간 23㎡ ÷ 0.46 = 50명. 상류 X→J: 분당 10,000명.
    const network = {
      schemaVersion: 1,
      nodes: [node('X', 'exit', { exitShare: 1 }), node('J', 'junction'), node('DA', 'destination', { direction: 'A' as const })],
      edges: [edge('e-xj', 'X', 'J'), edge('e-ja', 'J', 'DA', { widthM: 1, queueAreaM2: 23 })],
    };
    const routes = {
      schemaVersion: 1,
      routes: [{ direction: 'A' as const, name: 'A', destinationNodeId: 'DA', paths: [{ exitNodeId: 'X', edgeIds: ['e-xj', 'e-ja'] }] }],
    };
    const r = simulate({
      network,
      routes,
      assumptions: testAssumptions,
      attendance: 1500,
      egress: { kind: 'custom', fractions: Array(5).fill(0.2) }, // 분당 300명
      directionShare: { A: 1 },
      horizonMinutes: 30,
    });
    expect(Math.max(...r.edges['e-ja']!.queue)).toBeLessThanOrEqual(50 + 1e-6);
    expect(Math.max(...r.edges['e-xj']!.queue)).toBeGreaterThan(0);
    // 하류 통과량은 용량을 유지한다
    expect(Math.max(...r.edges['e-ja']!.passed)).toBeCloseTo(100, 6);
    // 결국 모두 도착한다
    expect(r.ledger.arrived[r.minutes - 1]!).toBeCloseTo(1500, 6);
  });
});

describe('우회 보행', () => {
  it('가까운 방면 대비 더 걸은 시간을 사람·분으로 더한다', () => {
    // A까지 10분(700m), B까지 20분(1,400m). A로 가던 1,000명 중 10%를 B로 안내 → 100명 × 10분
    const { network, routes } = forkNetwork({ lengthA: 700, lengthB: 1400, widthA: 100, widthB: 100 });
    const r = simulate({
      network,
      routes,
      assumptions: testAssumptions,
      attendance: 1000,
      egress: tenMinutesFlat,
      directionShare: { A: 1, B: 0 },
      guidance: { startMinute: 0, moves: [{ junctionNodeId: 'J', from: 'A', to: 'B', ratio: 0.1 }] },
      compliance: 1,
      horizonMinutes: 60,
    });
    expect(r.totals.detourMinutesTotal).toBeCloseTo(1000, 6);
    expect(r.totals.queueMinutesTotal).toBe(0);
    expect(r.totals.burdenPersonMinutes).toBeCloseTo(1000, 6);
    expect(r.totals.perPersonExtraMinutes).toBeCloseTo(1, 6);
  });

  it('걷는 시간만큼 늦게 도착한다', () => {
    const { network, routes } = forkNetwork({ lengthA: 700, widthA: 100 });
    const r = simulate({
      network,
      routes,
      assumptions: testAssumptions,
      attendance: 100,
      egress: { kind: 'custom', fractions: [1] },
      directionShare: { A: 1 },
      horizonMinutes: 20,
    });
    // 0분에 나와 700m ÷ 70m/분 = 10분 걸어 10분째에 도착
    expect(r.ledger.arrived[9]!).toBe(0);
    expect(r.ledger.arrived[10]!).toBeCloseTo(100, 6);
  });
});
