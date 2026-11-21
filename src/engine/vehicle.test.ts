import { describe, expect, it } from 'vitest';
import { simulateVehicleExits } from './vehicle';
import { simulate } from './simulate';
import { edge, node, tenMinutesFlat, testAssumptions } from './testing/fixtures';
import type { Parking, SimulationInput, VehicleExit } from './types';

const exits: VehicleExit[] = [
  { id: 'X', name: 'X', throughputPerMin: 25, crossingNodeIds: [], source: '가정' },
  { id: 'Y', name: 'Y', throughputPerMin: 25, crossingNodeIds: [], source: '가정' },
];
const fiftyPerMin = [...Array(10).fill(50), ...Array(20).fill(0)];

describe('가상 사례 2: 차량 10분간 분당 50대, 출구 각 분당 25대', () => {
  it('현재 운영안(X 40·Y 10)은 X 최대 150대, 대기 1,200대·분', () => {
    const r = simulateVehicleExits({ departures: fiftyPerMin, exits, share: { X: 0.8, Y: 0.2 } });
    const x = r.exits.find((e) => e.exitId === 'X')!;
    const y = r.exits.find((e) => e.exitId === 'Y')!;
    expect(x.maxQueue).toBeCloseTo(150, 6);
    expect(y.maxQueue).toBe(0);
    expect(r.queueVehicleMinutes).toBeCloseTo(1200, 6);
    expect(x.clearMinute).toBe(16);
  });

  it('분산안(X 30·Y 20)은 X 최대 50대, 대기 300대·분', () => {
    const r = simulateVehicleExits({ departures: fiftyPerMin, exits, share: { X: 0.6, Y: 0.4 } });
    expect(r.exits.find((e) => e.exitId === 'X')!.maxQueue).toBeCloseTo(50, 6);
    expect(r.queueVehicleMinutes).toBeCloseTo(300, 6);
  });
});

describe('차량을 포함한 시뮬레이션', () => {
  const parking: Parking = {
    schemaVersion: 1,
    lots: [{ id: 'P1', name: 'P1', capacity: 1000, exitIds: ['X', 'Y'], source: '관측' }],
    exits,
  };
  function base(extra: Partial<SimulationInput> = {}): SimulationInput {
    return {
      network: {
        schemaVersion: 1,
        nodes: [node('E', 'exit', { exitShare: 1 }), node('DA', 'destination', { direction: 'A' })],
        edges: [edge('e1', 'E', 'DA')],
      },
      routes: {
        schemaVersion: 1,
        routes: [{ direction: 'A', name: 'A', destinationNodeId: 'DA', paths: [{ exitNodeId: 'E', edgeIds: ['e1'] }] }],
      },
      assumptions: testAssumptions,
      attendance: 4000,
      egress: tenMinutesFlat,
      directionShare: { A: 1 },
      horizonMinutes: 30,
      // 4,000명 × 30% ÷ 2.4명 = 500대 → 10분간 분당 50대
      vehicle: { parking, ratio: 0.3, occupancy: 2.4, exitShare: { X: 0.8, Y: 0.2 } },
      ...extra,
    };
  }

  it('차량 대기 대·분에 탑승 인원을 곱해 사람·분으로 합산한다', () => {
    const r = simulate(base());
    expect(r.vehicle!.vehicles).toBeCloseTo(500, 6);
    expect(r.totals.vehicleQueuePersonMinutes).toBeCloseTo(1200 * 2.4, 6);
    expect(r.totals.burdenPersonMinutes).toBeCloseTo(
      r.totals.queueMinutesTotal + r.totals.detourMinutesTotal + 1200 * 2.4,
      6,
    );
  });

  it('차로 가는 관객은 지하철 방면 보행 인원에서 빠진다', () => {
    const r = simulate(base());
    expect(r.ledger.departed[r.minutes - 1]!).toBeCloseTo(4000 * 0.7, 6);
  });

  it('출구 배정을 주지 않으면 주차장 용량을 출구에 똑같이 나눈다', () => {
    const r = simulate(base({ vehicle: { parking, ratio: 0.3, occupancy: 2.4 } }));
    expect(r.vehicle!.exits.find((e) => e.exitId === 'X')!.share).toBeCloseTo(0.5, 9);
  });
});

describe('보행·차량 교차점', () => {
  // 출구 E → 교차점 C → 역 DA. C→DA는 분당 200명. 차량 출구 G(분당 10대)가 C를 지난다.
  function crossingInput(crossingNodeIds: string[]): SimulationInput {
    return {
      network: {
        schemaVersion: 1,
        nodes: [node('E', 'exit', { exitShare: 1 }), node('C', 'crossing'), node('DA', 'destination', { direction: 'A' })],
        edges: [edge('e-ec', 'E', 'C'), edge('e-cd', 'C', 'DA', { widthM: 2 })],
      },
      routes: {
        schemaVersion: 1,
        routes: [{ direction: 'A', name: 'A', destinationNodeId: 'DA', paths: [{ exitNodeId: 'E', edgeIds: ['e-ec', 'e-cd'] }] }],
      },
      assumptions: testAssumptions, // 교차점에서 보행 60%, 차량 40%
      attendance: 2000,
      egress: tenMinutesFlat,
      directionShare: { A: 1 },
      horizonMinutes: 40,
      vehicle: {
        parking: {
          schemaVersion: 1,
          lots: [{ id: 'P1', name: 'P1', capacity: 1000, exitIds: ['G'], source: '관측' }],
          exits: [{ id: 'G', name: 'G', throughputPerMin: 10, crossingNodeIds, source: '가정' }],
        },
        // 2,000명 × 25% = 500명 → 보행 1,500명(분당 150명), 차량 500 ÷ 10 = 50대(분당 5대)
        ratio: 0.25,
        occupancy: 10,
      },
    };
  }

  it('교차점이 없으면 보행 분당 150명 < 200명, 차량 5대 < 10대로 대기가 없다', () => {
    const r = simulate(crossingInput([]));
    expect(r.totals.maxQueue).toBe(0);
    expect(r.vehicle!.exits[0]!.maxQueue).toBe(0);
  });

  it('교차점을 나눠 쓰면 보행(200×0.6=120명)과 차량(10×0.4=4대) 모두 대기가 생긴다', () => {
    const r = simulate(crossingInput(['C']));
    expect(r.edges['e-cd']!.queue.some((q) => q > 0)).toBe(true);
    expect(r.vehicle!.exits[0]!.maxQueue).toBeGreaterThan(0);
  });
});
