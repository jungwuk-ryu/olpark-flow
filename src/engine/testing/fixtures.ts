import type { Assumptions, Network, NetworkEdge, NetworkNode, Routes } from '../types';

/** 테스트용 가정값: 폭 1m당 분당 100명이라 폭 2m = 분당 200명으로 계산이 쉽다. */
export const testAssumptions: Assumptions = {
  schemaVersion: 1,
  walkSpeedMPerMin: 70,
  flowPerMeterPerMin: { flat: 100, ramp: 100, gate: 100, stairsFactor: 0.6 },
  weatherFactor: { 맑음: 1, 우천: 0.85 },
  jamAreaPerPersonM2: 0.46,
  crossingPedestrianShare: 0.6,
  vehicleOccupancy: 2.4,
  staffPerJunction: 2,
  walkToLotMinutes: 0,
};

export function node(id: string, kind: NetworkNode['kind'], extra: Partial<NetworkNode> = {}): NetworkNode {
  return { id, kind, name: id, lngLat: [127.12, 37.52], source: '가정', ...extra };
}

export function edge(id: string, from: string, to: string, extra: Partial<NetworkEdge> = {}): NetworkEdge {
  return {
    id,
    from,
    to,
    lengthM: 0,
    widthM: 100,
    type: 'flat',
    queueAreaM2: 1e7,
    stepFree: true,
    source: '가정',
    ...extra,
  };
}

/**
 * 출구 X → 분기점 J → 방면 A / B.
 * J→A, J→B는 폭 2m(분당 200명) 병목. 길이는 기본 0 (걷는 시간 없음).
 */
export function forkNetwork(opts: { lengthA?: number; lengthB?: number; widthA?: number; widthB?: number } = {}): {
  network: Network;
  routes: Routes;
} {
  return {
    network: {
      schemaVersion: 1,
      nodes: [
        node('X', 'exit', { exitShare: 1, venueId: 'kspo-dome' }),
        node('J', 'junction'),
        node('DA', 'destination', { direction: 'A' }),
        node('DB', 'destination', { direction: 'B' }),
      ],
      edges: [
        edge('e-xj', 'X', 'J'),
        edge('e-ja', 'J', 'DA', { widthM: opts.widthA ?? 2, lengthM: opts.lengthA ?? 0 }),
        edge('e-jb', 'J', 'DB', { widthM: opts.widthB ?? 2, lengthM: opts.lengthB ?? 0 }),
      ],
    },
    routes: {
      schemaVersion: 1,
      routes: [
        { direction: 'A', name: 'A', destinationNodeId: 'DA', paths: [{ exitNodeId: 'X', edgeIds: ['e-xj', 'e-ja'] }] },
        { direction: 'B', name: 'B', destinationNodeId: 'DB', paths: [{ exitNodeId: 'X', edgeIds: ['e-xj', 'e-jb'] }] },
      ],
    },
  };
}

/** 10분 동안 매분 같은 인원이 나오는 퇴장 곡선 */
export const tenMinutesFlat = { kind: 'custom' as const, fractions: Array(10).fill(0.1) };
