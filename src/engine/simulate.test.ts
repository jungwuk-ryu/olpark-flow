import { describe, expect, it } from 'vitest';
import { simulate } from './simulate';
import { edge, forkNetwork, node, tenMinutesFlat, testAssumptions } from './testing/fixtures';
import type { Parking, SimulationInput } from './types';

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

  it('전체 대기 최댓값(모든 병목 합)과 그 시각을 낸다', () => {
    const r = simulate(caseOne());
    expect(r.totals.peakQueueTotal).toBeCloseTo(1000, 6);
    expect(r.totals.peakQueueTotalMinute).toBe(10);
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
    // 한 지점 최대는 공간 한계에 막히지만, 전체 대기는 넘친 줄까지 센다
    expect(r.totals.peakQueueTotal).toBeGreaterThan(r.totals.maxQueue);
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

describe('귀가 수단: 역 방면 길에는 지하철을 타러 가는 사람만 걷는다', () => {
  const parking: Parking = {
    schemaVersion: 1,
    lots: [{ id: 'P1', name: 'P1', capacity: 5000, exitIds: ['G'], source: '가정' }],
    exits: [{ id: 'G', name: 'G', throughputPerMin: 1000, crossingNodeIds: [], source: '가정' }],
  };

  it('지하철 이용 비율 50%면 길에는 절반만 나오고, 나머지는 인원 장부의 other로 남는다', () => {
    const r = simulate(caseOne({ stationShare: 0.5 }));
    // A 150명/분, B 50명/분으로 두 병목(분당 200명) 모두 줄이 없다
    expect(r.totals.maxQueue).toBe(0);
    expect(r.departures.reduce((x, y) => x + y, 0)).toBeCloseTo(2000, 6);
    expect(r.totals.directionPeople.A).toBeCloseTo(1500, 6);
    expect(r.totals.modePeople).toEqual({ station: 2000, vehicle: 0, other: 2000 });
  });

  it('관객 4,000명 중 50%가 역으로 가는 것은 2,000명 전원이 역으로 가는 것과 같은 흐름이다', () => {
    const part = simulate(caseOne({ attendance: 12000, stationShare: 0.5 }));
    const whole = simulate(caseOne({ attendance: 6000 }));
    expect(part.edges['e-ja']!.queue).toEqual(whole.edges['e-ja']!.queue);
    expect(part.queueTotal).toEqual(whole.queueTotal);
    expect(part.totals.clearMinute).toBe(whole.totals.clearMinute);
  });

  it('지하철 이용 비율이 없으면 차량을 뺀 전원이 역으로 간다', () => {
    const r = simulate(caseOne({ vehicle: { parking, ratio: 0.25 } }));
    expect(r.totals.modePeople.station).toBeCloseTo(3000, 6);
    expect(r.totals.modePeople.vehicle).toBeCloseTo(1000, 6);
    expect(r.totals.modePeople.other).toBeCloseTo(0, 6);
  });

  it('지하철·차량·그 밖 인원의 합은 관객 수다', () => {
    const r = simulate(caseOne({ stationShare: 0.42, vehicle: { parking, ratio: 0.19 } }));
    const m = r.totals.modePeople;
    expect(m.station).toBeCloseTo(1680, 6);
    expect(m.vehicle).toBeCloseTo(760, 6);
    expect(m.other).toBeCloseTo(1560, 6);
    expect(m.station + m.vehicle + m.other).toBeCloseTo(4000, 6);
    // 차량 대수는 지하철 이용 비율과 무관하다
    expect(r.vehicle!.vehicles).toBeCloseTo(760 / testAssumptions.vehicleOccupancy, 6);
  });

  it('지하철 이용 비율과 차량 비율의 합이 1을 넘으면 경고하고 지하철 쪽을 줄인다', () => {
    const r = simulate(caseOne({ stationShare: 0.9, vehicle: { parking, ratio: 0.25 } }));
    expect(r.totals.modePeople.station).toBeCloseTo(3000, 6);
    expect(r.totals.modePeople.other).toBeCloseTo(0, 6);
    expect(r.warnings.some((w) => w.includes('지하철 이용 비율'))).toBe(true);
  });

  it('1인당 추가 시간은 계산에 넣은 사람 수로 나눈다', () => {
    // 역으로 가는 4,000명이 가상 사례 1과 같은 줄을 만든다: 대기 7,500사람·분
    const r = simulate(caseOne({ attendance: 8000, stationShare: 0.5 }));
    expect(r.totals.queueMinutesTotal).toBeCloseTo(7500, 6);
    expect(r.totals.perPersonExtraMinutes).toBeCloseTo(7500 / 4000, 9);
  });
});
