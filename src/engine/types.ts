/**
 * 계산 엔진 타입. 입력 데이터 모양은 docs/data-contract.md와 1:1로 맞춘다.
 * 엔진은 UI·네트워크 접근 없이 순수 함수로만 동작한다.
 */

export type Source = '예측' | '입력' | '가정' | '관측';
export type Direction = 'A' | 'B' | 'C';
export const DIRECTIONS: Direction[] = ['A', 'B', 'C'];

// ---------- 길 데이터 (팀원 B) ----------

export type NodeKind = 'exit' | 'junction' | 'bottleneck' | 'crossing' | 'destination';

export interface NetworkNode {
  id: string;
  kind: NodeKind;
  name: string;
  lngLat: [number, number];
  venueId?: string;
  exitShare?: number;
  direction?: Direction;
  source: Source;
}

export type EdgeType = 'flat' | 'stairs' | 'ramp' | 'gate';

export interface NetworkEdge {
  id: string;
  from: string;
  to: string;
  lengthM: number;
  widthM: number;
  type: EdgeType;
  queueAreaM2: number;
  stepFree: boolean;
  path?: [number, number][];
  source: Source;
}

export interface Network {
  schemaVersion: number;
  coordSystemOriginal?: string;
  source?: Source;
  nodes: NetworkNode[];
  edges: NetworkEdge[];
}

export interface RoutePath {
  exitNodeId: string;
  edgeIds: string[];
}

export interface Route {
  direction: Direction;
  name: string;
  line?: string;
  destinationNodeId: string;
  paths: RoutePath[];
}

export interface Routes {
  schemaVersion: number;
  routes: Route[];
}

export interface ParkingLot {
  id: string;
  name: string;
  capacity: number;
  exitIds: string[];
  source: Source;
}

export interface VehicleExit {
  id: string;
  name: string;
  throughputPerMin: number;
  crossingNodeIds: string[];
  source: Source;
}

export interface Parking {
  schemaVersion: number;
  lots: ParkingLot[];
  exits: VehicleExit[];
}

export interface Assumptions {
  schemaVersion: number;
  walkSpeedMPerMin: number;
  flowPerMeterPerMin: { flat: number; ramp: number; gate: number; stairsFactor: number };
  weatherFactor: Record<string, number>;
  jamAreaPerPersonM2: number;
  crossingPedestrianShare: number;
  vehicleOccupancy: number;
  /** 분기점 하나를 운영하는 데 필요한 안내요원 수 */
  staffPerJunction: number;
  /** 주차장까지 걸어가는 데 걸리는 평균 시간 (차량 출발 지연) */
  walkToLotMinutes: number;
  notes?: Record<string, string>;
}

// ---------- 시뮬레이션 입력 ----------

export type EgressCurveInput =
  | { kind: 'gamma'; totalMinutes: number; peakMinute: number }
  /** 분마다 나오는 비율. 합이 1이 아니면 정규화한다. */
  | { kind: 'custom'; fractions: number[] };

/** 분기점에서 from 방면으로 가던 사람 중 ratio만큼을 to 방면으로 안내한다. */
export interface GuidanceMove {
  junctionNodeId: string;
  from: Direction;
  to: Direction;
  ratio: number;
}

export interface GuidancePlan {
  moves: GuidanceMove[];
  /** 공연 종료 후 몇 분부터 안내를 시작하는지 (그 시각 이후 출구를 나온 사람에게 적용) */
  startMinute: number;
}

export interface SimulationInput {
  network: Network;
  routes: Routes;
  assumptions: Assumptions;
  attendance: number;
  egress: EgressCurveInput;
  /** 안내가 없을 때의 방면 비율 (합 = 1) */
  directionShare: Partial<Record<Direction, number>>;
  guidance?: GuidancePlan;
  /** 안내를 받은 사람 중 실제로 따르는 비율 (0~1). 안내요원 효과는 이 값으로만 반영한다. */
  compliance?: number;
  blockedEdgeIds?: string[];
  /** 통제·부스로 유효 폭이 바뀐 길 토막 (m) */
  edgeWidthOverrides?: Record<string, number>;
  weather?: string;
  /** 계산할 분 수 (기본 90) */
  horizonMinutes?: number;
  vehicle?: VehicleInput;
}

export interface VehicleInput {
  parking: Parking;
  /** 차량으로 귀가하는 관객 비율 */
  ratio: number;
  /** 차 1대당 탑승 인원. 없으면 assumptions.vehicleOccupancy */
  occupancy?: number;
  /** 출구별 배정 비율. 없으면 주차장 용량 기준 기본 배정 */
  exitShare?: Record<string, number>;
}

// ---------- 시뮬레이션 결과 ----------

export type Status = '여유' | '보통' | '병목' | '위험';

export interface EdgeSeries {
  edgeId: string;
  capacityPerMin: number;
  arrivals: number[];
  passed: number[];
  queue: number[];
  saturation: number[];
  areaPerPersonM2: (number | null)[];
  status: Status[];
}

export interface VehicleExitSeries {
  exitId: string;
  share: number;
  arrivals: number[];
  passed: number[];
  queue: number[];
  maxQueue: number;
  clearMinute: number | null;
  status: Status[];
}

export interface Totals {
  /** 한 지점에서 가장 길었던 줄 (명) */
  maxQueue: number;
  maxQueueEdgeId: string | null;
  maxQueueMinute: number;
  /** 대기시간 합계 (사람·분) */
  queueMinutesTotal: number;
  /** 가장 가까운 방면 대비 더 걸은 시간 합계 (사람·분) */
  detourMinutesTotal: number;
  /** 차량 대기 대·분 × 탑승 인원 (사람·분) */
  vehicleQueuePersonMinutes: number;
  /** 대기 + 추가 보행 + 차량 대기 (사람·분) */
  burdenPersonMinutes: number;
  /** 1인당 추가 시간 (분) */
  perPersonExtraMinutes: number;
  /** 모든 보행 대기가 풀린 시각 (종료 후 분). 계산 범위 안에 안 풀리면 null */
  clearMinute: number | null;
  /** 실제 방면 비율 (안내·통제 반영 후) */
  directionShareActual: Record<Direction, number>;
  /** 방면별 배정 인원 */
  directionPeople: Record<Direction, number>;
}

export interface SimulationResult {
  minutes: number;
  /** 분마다 공연장을 나온 인원 */
  departures: number[];
  edges: Record<string, EdgeSeries>;
  /** 분마다 전체 보행 대기 인원 합 */
  queueTotal: number[];
  vehicle: { exits: VehicleExitSeries[]; occupancy: number; vehicles: number } | null;
  totals: Totals;
  /** 막혀서 계산에서 빠진 방면 */
  unavailableDirections: Direction[];
  warnings: string[];
  /**
   * 인원 장부 (분마다 그 분이 끝난 시점 기준).
   * departed·arrived는 누적, inTransit·queued는 그 시점 인원. departed = arrived + inTransit + queued.
   */
  ledger: { departed: number[]; arrived: number[]; inTransit: number[]; queued: number[] };
}
