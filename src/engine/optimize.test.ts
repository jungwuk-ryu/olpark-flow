import { describe, expect, it } from 'vitest';
import { optimize } from './optimize';
import { forkNetwork, node, edge, tenMinutesFlat, testAssumptions } from './testing/fixtures';
import type { SimulationInput } from './types';

function caseOne(extra: Partial<SimulationInput> = {}, fork: Parameters<typeof forkNetwork>[0] = {}): SimulationInput {
  const { network, routes } = forkNetwork(fork);
  return {
    network,
    routes,
    assumptions: testAssumptions,
    attendance: 4000,
    egress: tenMinutesFlat,
    directionShare: { A: 0.75, B: 0.25 },
    compliance: 1,
    horizonMinutes: 30,
    ...extra,
  };
}

const totalRatio = (r: ReturnType<typeof optimize>) => r.best.plan.guidance.moves.reduce((s, m) => s + m.ratio, 0);

describe('optimize', () => {
  it('A 집중을 B로 나눠 대기 + 추가 보행 합계를 줄인다', () => {
    const r = optimize(caseOne(), { staff: 2 });
    expect(r.best.result.totals.burdenPersonMinutes).toBeLessThan(r.current.totals.burdenPersonMinutes);
    // 손으로 만든 분산안(비율 0.2 → 2,400사람·분)보다 나쁘지 않다
    expect(r.best.result.totals.burdenPersonMinutes).toBeLessThanOrEqual(2400);
    const move = r.best.plan.guidance.moves[0]!;
    expect(move).toMatchObject({ junctionNodeId: 'J', from: 'A', to: 'B' });
    expect(move.ratio).toBeGreaterThanOrEqual(0.3);
    expect(move.ratio).toBeLessThanOrEqual(0.4);
  });

  it('안내요원이 분기점 하나를 운영할 만큼 없으면 안내를 바꾸지 않는다', () => {
    const r = optimize(caseOne(), { staff: 1 }); // 분기점 1곳 = 2명 필요
    expect(r.best.plan.guidance.moves).toEqual([]);
    expect(r.best.result.totals.burdenPersonMinutes).toBeCloseTo(r.current.totals.burdenPersonMinutes, 6);
  });

  it('안내요원은 길 용량을 늘리지 않는다', () => {
    const few = optimize(caseOne(), { staff: 2 });
    const many = optimize(caseOne(), { staff: 12 });
    expect(many.best.result.edges['e-ja']!.capacityPerMin).toBe(few.best.result.edges['e-ja']!.capacityPerMin);
    expect(many.best.result.totals.burdenPersonMinutes).toBeCloseTo(few.best.result.totals.burdenPersonMinutes, 6);
  });

  it('안내 반응률이 0이면 안내 효과가 없어 바꾸지 않는 안을 고른다', () => {
    const r = optimize(caseOne({ compliance: 0 }), { staff: 6 });
    expect(totalRatio(r)).toBe(0);
  });

  it('운영하지 않기로 한 방면으로는 안내하지 않는다', () => {
    const { network, routes } = forkNetwork();
    // 분기점에서 A·B·C로 나뉘는 길
    network.nodes.push(node('DC', 'destination', { direction: 'C' }));
    network.edges.push(edge('e-jc', 'J', 'DC', { widthM: 2 }));
    routes.routes.push({ direction: 'C', name: 'C', destinationNodeId: 'DC', paths: [{ exitNodeId: 'X', edgeIds: ['e-xj', 'e-jc'] }] });
    const input = caseOne({ network, routes, directionShare: { A: 0.75, B: 0.15, C: 0.1 } });
    const r = optimize(input, { staff: 2, allowedDirections: ['A', 'B'] });
    expect(r.best.plan.guidance.moves.length).toBeGreaterThan(0);
    expect(r.best.plan.guidance.moves.some((m) => m.to === 'C')).toBe(false);
  });

  it('막힌 방면으로는 안내하지 않는다', () => {
    const r = optimize(caseOne({ blockedEdgeIds: ['e-jb'] }), { staff: 6 });
    expect(r.best.plan.guidance.moves.some((m) => m.to === 'B')).toBe(false);
  });

  it('1인당 추가 보행 상한을 넘는 안내는 하지 않는다', () => {
    // B가 A보다 10분 더 걸린다. 상한 5분이면 A→B 안내 불가
    const r = optimize(caseOne({}, { lengthA: 700, lengthB: 1400 }), { staff: 6, maxDetourPerPersonMinutes: 5 });
    expect(r.best.plan.guidance.moves).toEqual([]);
  });

  it('차량 출구마다 최소 배정 비율(기본 15%)을 지킨다', () => {
    // Z는 분당 1대라 배정하지 않는 게 유리하지만, 출구를 사실상 닫는 안은 고르지 않는다
    const input: SimulationInput = {
      ...caseOne(),
      vehicle: {
        parking: {
          schemaVersion: 1,
          lots: [{ id: 'P1', name: 'P1', capacity: 1000, exitIds: ['X', 'Y', 'Z'], source: '관측' }],
          exits: [
            { id: 'X', name: 'X', throughputPerMin: 25, crossingNodeIds: [], source: '가정' },
            { id: 'Y', name: 'Y', throughputPerMin: 25, crossingNodeIds: [], source: '가정' },
            { id: 'Z', name: 'Z', throughputPerMin: 1, crossingNodeIds: [], source: '가정' },
          ],
        },
        ratio: 0.3,
        occupancy: 2.4,
      },
    };
    const r = optimize(input, { staff: 2 });
    for (const v of Object.values(r.best.plan.vehicleExitShare!)) expect(v).toBeGreaterThanOrEqual(0.15 - 1e-9);
    const loose = optimize(input, { staff: 2, minVehicleExitShare: 0 });
    expect(loose.best.plan.vehicleExitShare!.Z).toBe(0);
  });

  it('차량 출구 배정을 고르게 나눠 차량 대기를 없앤다', () => {
    const input: SimulationInput = {
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
      vehicle: {
        parking: {
          schemaVersion: 1,
          lots: [{ id: 'P1', name: 'P1', capacity: 1000, exitIds: ['X', 'Y'], source: '관측' }],
          exits: [
            { id: 'X', name: 'X', throughputPerMin: 25, crossingNodeIds: [], source: '가정' },
            { id: 'Y', name: 'Y', throughputPerMin: 25, crossingNodeIds: [], source: '가정' },
          ],
        },
        ratio: 0.3,
        occupancy: 2.4,
        exitShare: { X: 0.8, Y: 0.2 },
      },
    };
    const r = optimize(input, { staff: 2 });
    expect(r.current.totals.vehicleQueuePersonMinutes).toBeCloseTo(2880, 6);
    expect(r.best.plan.vehicleExitShare!.X).toBeCloseTo(0.5, 6);
    expect(r.best.result.totals.vehicleQueuePersonMinutes).toBe(0);
  });
});
