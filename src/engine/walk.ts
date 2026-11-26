import { egressFractions } from './egress';
import { statusOf } from './status';
import type { Assumptions, Direction, EgressCurveInput, Source, Status } from './types';

/**
 * 공원 전체 보행로 위의 보행 흐름 계산.
 *
 * 첫 엔진(simulate.ts)은 정해 둔 세 경로만 다룬다. 여기서는 보행로 그래프 전체에서 사람마다 가장 수월한 길을 찾아 흘려 보내고,
 * 길마다 통과 인원과 대기를 낸다. 길을 막거나 장애물(폭을 줄임)을 놓거나 안전요원을 세우면 길을 다시 찾는다.
 *
 * 모델
 * - 무리(trip) = 출구 한 곳에서 목적지 한 곳으로 가는 사람들. 길 종류별 비용(cost)을 곱한 거리가 가장 짧은 길로 간다.
 * - 그 길과 거의 같은 길(ALTERNATIVES.tolerance 안)이 더 있으면 나눠 간다. 몫은 서로 다른 구간의 가장 좁은 폭에 비례하고,
 *   더 먼 길일수록 줄어든다. 나란한 두 길 가운데 몇 m 짧은 쪽으로 모두 몰리는 일을 막는 가정이다.
 * - 길 토막마다 입구에서 1분에 (유효 폭 × 폭 1m당 통과 인원)만큼 들어간다. 마주 오는 흐름은 같은 용량을 나눠 쓴다.
 * - 다음 토막에 들어가지 못한 사람은 지금 있는 토막 위에 서서 기다린다. 토막이 꽉 차면(폭 × 길이 ÷ 정체 시 1인당 면적)
 *   그 토막으로도 더 들어오지 못해 줄이 뒤쪽 길로 번진다. 토막 위의 사람 = 서서 기다리는 사람 + 걷는 사람
 *   (1분에 들어온 사람 × 그 토막을 걷는 데 드는 분). 걷는 시간은 1분 단위로 반올림해 늦추지만, 토막 위 인원은 실제 길이로 센다.
 * - 막은 길은 아무도 지나지 못한다. 좁힌 길은 남은 폭만큼만 지난다.
 * - 안전요원을 세운 길: 안내를 따르는 사람(반응률)은 그 길 앞에서 다른 길로 돌아가거나 안내받은 역으로 간다. 나머지는 그대로 간다.
 * - 목적지에 받는 한도(sink)가 있으면 그 앞에 줄이 선다 (역 개찰구).
 * - 길의 상태: 들어오려는 사람이 통과 용량의 70% 이상이면 보통, 100% 이상이면 병목.
 *   길 위에 선 사람이 정체 밀도에 가까우면 위험 (DANGER_STRETCH_M보다 짧은 토막은 그 길이로 본다).
 */

export type WalkKind = 'plaza' | 'walk' | 'sidewalk' | 'crossing' | 'stairs' | 'trail' | 'road' | 'aisle';
export type WalkMode = 'rail' | 'bus' | 'car';
export type LngLat = [number, number];

export interface WalkNode {
  id: string;
  lngLat: LngLat;
}

export interface WalkEdge {
  id: string;
  a: string;
  b: string;
  lengthM: number;
  widthM: number;
  /** 폭의 출처: 항공영상 판독, OpenStreetMap 태그, 길 종류별 기본값(가정) */
  widthSource: '판독' | 'OSM' | '가정';
  kind: WalkKind;
  /** 길 고르기에서 거리에 곱하는 값 (클수록 피한다) */
  cost: number;
  /** a에서 b까지의 꺾인 선. 없으면 두 점을 곧게 잇는다 */
  path?: LngLat[];
  /** 첫 길 데이터(network.json)의 어느 토막을 따라가는지 */
  baseEdgeId?: string;
}

export interface WalkExit {
  id: string;
  name: string;
  venueId?: string;
  share?: number;
  nodeId: string;
}

export interface WalkDestination {
  id: string;
  mode: WalkMode;
  name: string;
  lngLat: LngLat;
  /** 이 목적지로 들어가는 그래프의 점들 (역은 출입구마다 하나). 가장 가까운 곳으로 간다 */
  nodeIds: string[];
  direction?: Direction;
  line?: string;
}

export interface WalkGraph {
  schemaVersion: number;
  source: string;
  osmBase?: string;
  nodes: WalkNode[];
  edges: WalkEdge[];
  exits: WalkExit[];
  destinations: WalkDestination[];
}

/** 한 출구에서 한 목적지로 가는 사람들 */
export interface WalkTrip {
  id: string;
  exitId: string;
  destinationId: string;
  mode: WalkMode;
  people: number;
  /** 이 무리가 나오기 시작하는 시각 (계산의 0분 기준 분). 기본 0 */
  startMinute?: number;
}

export interface WalkStaff {
  edgeId: string;
  /** 지하철 이용객을 보낼 역(목적지 ID). 없으면 이 길을 피해 돌아가게만 안내한다 */
  sendTo?: string;
}

/** 운영자가 지도에 놓은 것 */
export interface WalkControls {
  blocked: string[];
  /** 길 ID → 남은 유효 폭 (m) */
  narrowed: Record<string, number>;
  staffed: WalkStaff[];
  /** 안내를 따르는 사람의 비율 (0~1) */
  compliance: number;
}

export const NO_CONTROLS: WalkControls = { blocked: [], narrowed: {}, staffed: [], compliance: 0.6 };

/** 목적지가 1분에 받아들이는 인원 (역 개찰구 등) */
export interface WalkSink {
  capacityPerMin: number;
  source: Source;
}

export interface WalkInput {
  graph: WalkGraph;
  trips: WalkTrip[];
  egress: EgressCurveInput;
  assumptions: Assumptions;
  controls?: WalkControls;
  /** 목적지 ID → 받는 한도 */
  sinks?: Record<string, WalkSink>;
  weather?: string;
  /** 계산할 분 수 (기본 120) */
  horizonMinutes?: number;
}

export interface WalkEdgeSeries {
  edgeId: string;
  capacityPerMin: number;
  /** 분마다 이 길 입구에 닿은 사람 */
  arrivals: number[];
  /** 분마다 이 길에 실제로 들어선 사람 */
  passed: number[];
  /**
   * 분마다 이 길 위를 지나던 사람 (명/분). 지도의 통행량 색이 이 값이다.
   * 걷는 데 여러 분이 걸리는 긴 토막은 들어선 사람을 걷는 분 수에 고르게 나눠 센다. 그래서 들어오는 사람이 끊겨도
   * 마지막 사람이 다 걸을 때까지 0이 되지 않는다. 짧은 토막에서는 passed와 같고, 저녁 전체를 더하면 people과 같다
   */
  flow: number[];
  /** 그 분이 끝났을 때 이 길에 들어가려고 기다리는 사람 */
  queue: number[];
  /** 그 분이 끝났을 때 이 길 위에 있는 사람 (걷는 사람과 다음 길을 기다리는 사람) */
  occupancy: number[];
  saturation: number[];
  status: Status[];
  /** 지나간 사람 합계 */
  people: number;
  peakQueue: number;
  /** 저녁 전체에서 가장 나빴던 상태 */
  worst: Status;
}

export interface WalkSinkSeries {
  destinationId: string;
  capacityPerMin: number;
  queue: number[];
  passed: number[];
  peakQueue: number;
}

/** 길을 찾은 결과: 한 무리가 실제로 간 길 */
export interface WalkRoute {
  tripId: string;
  /** base = 원래 길, guided = 안내를 따른 길 */
  kind: 'base' | 'guided';
  people: number;
  destinationId: string;
  edgeIds: string[];
  walkMinutes: number;
  lengthM: number;
}

export interface WalkTotals {
  /** 계산에 넣은 사람 */
  people: number;
  /** 길이 막혀 목적지에 갈 수 없는 사람 */
  stranded: number;
  /** 가장 붐빌 때 모든 줄에 서 있는 사람 합과 그 시각 */
  peakQueueTotal: number;
  peakQueueTotalMinute: number;
  /** 대기시간 합계 (사람·분) */
  queueMinutesTotal: number;
  /** 안내를 따라 더 걸은 시간 합계 (사람·분) */
  detourMinutesTotal: number;
  /** 모든 줄이 풀린 시각. 계산 범위 안에 안 풀리면 null */
  clearMinute: number | null;
  /** 안내를 따라 길이나 역을 바꾼 사람 */
  guided: number;
  /** 평균 보행 시간 (분, 대기 제외) */
  meanWalkMinutes: number;
}

export interface WalkResult {
  minutes: number;
  edges: Record<string, WalkEdgeSeries>;
  sinks: Record<string, WalkSinkSeries>;
  /** 분마다 모든 줄에 서 있는 사람 합 (길 + 목적지 앞) */
  queueTotal: number[];
  routes: WalkRoute[];
  totals: WalkTotals;
  /** 목적지별 도착 인원 */
  arrivedBy: Record<string, number>;
  /** 갈 길이 없는 무리 */
  stranded: { tripId: string; people: number }[];
  ledger: { departed: number[]; arrived: number[]; inTransit: number[]; queued: number[] };
  warnings: string[];
}

// ---------- 그래프 색인 ----------

interface Indexed {
  nodeIndex: Map<string, number>;
  edgeIndex: Map<string, number>;
  /** 점마다 [길 번호, 건너편 점 번호] */
  around: [number, number][][];
}

const indexes = new WeakMap<WalkGraph, Indexed>();

function indexOf(graph: WalkGraph): Indexed {
  const cached = indexes.get(graph);
  if (cached) return cached;
  const nodeIndex = new Map(graph.nodes.map((n, i) => [n.id, i]));
  const edgeIndex = new Map(graph.edges.map((e, i) => [e.id, i]));
  const around: [number, number][][] = graph.nodes.map(() => []);
  graph.edges.forEach((e, i) => {
    const a = nodeIndex.get(e.a);
    const b = nodeIndex.get(e.b);
    if (a === undefined || b === undefined) throw new Error(`보행로 ${e.id}의 끝점이 그래프에 없습니다`);
    around[a]!.push([i, b]);
    around[b]!.push([i, a]);
  });
  const made = { nodeIndex, edgeIndex, around };
  indexes.set(graph, made);
  return made;
}

/** 작은 값이 먼저 나오는 더미 */
class Heap {
  private items: [number, number][] = [];
  get size() {
    return this.items.length;
  }
  push(key: number, value: number) {
    const a = this.items;
    a.push([key, value]);
    let i = a.length - 1;
    while (i > 0) {
      const up = (i - 1) >> 1;
      if (a[up]![0] <= a[i]![0]) break;
      [a[up], a[i]] = [a[i]!, a[up]!];
      i = up;
    }
  }
  pop(): [number, number] {
    const a = this.items;
    const top = a[0]!;
    const last = a.pop()!;
    if (a.length) {
      a[0] = last;
      let i = 0;
      for (;;) {
        const l = 2 * i + 1;
        const r = l + 1;
        let m = i;
        if (l < a.length && a[l]![0] < a[m]![0]) m = l;
        if (r < a.length && a[r]![0] < a[m]![0]) m = r;
        if (m === i) break;
        [a[m], a[i]] = [a[i]!, a[m]!];
        i = m;
      }
    }
    return top;
  }
}

export interface FoundPath {
  /** 지나는 길 ID (순서대로) */
  edgeIds: string[];
  /** 길마다 a→b로 가면 true */
  forward: boolean[];
  /** 닿은 점 */
  nodeId: string;
  lengthM: number;
  /** 길 종류별 비용을 곱한 거리 */
  cost: number;
}

/**
 * from에서 targets 가운데 가장 수월하게 닿는 곳까지의 길. 못 가면 null.
 * closed의 길은 지나지 않는다. weight가 있으면 그 길의 비용에 곱해 길을 고른다 (돌려주는 cost는 곱하기 전 값).
 */
export function findPath(
  graph: WalkGraph,
  from: string,
  targets: Iterable<string>,
  closed: ReadonlySet<string> = new Set(),
  weight?: ReadonlyMap<string, number>,
): FoundPath | null {
  const ix = indexOf(graph);
  const start = ix.nodeIndex.get(from);
  const goal = new Set<number>();
  for (const id of targets) {
    const i = ix.nodeIndex.get(id);
    if (i !== undefined) goal.add(i);
  }
  if (start === undefined || !goal.size) return null;
  const best = new Float64Array(graph.nodes.length).fill(Infinity);
  const cameBy = new Int32Array(graph.nodes.length).fill(-1);
  const cameFrom = new Int32Array(graph.nodes.length).fill(-1);
  const heap = new Heap();
  best[start] = 0;
  heap.push(0, start);
  let end = -1;
  while (heap.size) {
    const [d, u] = heap.pop();
    if (d > best[u]!) continue;
    if (goal.has(u)) {
      end = u;
      break;
    }
    for (const [ei, v] of ix.around[u]!) {
      const e = graph.edges[ei]!;
      if (closed.has(e.id)) continue;
      const nd = d + e.lengthM * e.cost * (weight?.get(e.id) ?? 1);
      if (nd < best[v]!) {
        best[v] = nd;
        cameBy[v] = ei;
        cameFrom[v] = u;
        heap.push(nd, v);
      }
    }
  }
  if (end < 0) return null;
  const edgeIds: string[] = [];
  const forward: boolean[] = [];
  let lengthM = 0;
  let cost = 0;
  for (let v = end; v !== start; v = cameFrom[v]!) {
    const e = graph.edges[cameBy[v]!]!;
    edgeIds.push(e.id);
    forward.push(ix.nodeIndex.get(e.b) === v);
    lengthM += e.lengthM;
    cost += e.lengthM * e.cost;
  }
  edgeIds.reverse();
  forward.reverse();
  return { edgeIds, forward, nodeId: graph.nodes[end]!.id, lengthM, cost };
}

/**
 * 거의 같은 길로 나눠 가기 (가정).
 * tolerance: 가장 수월한 길보다 이 비율까지 더 먼 길은 함께 쓴다. 딱 그만큼 더 먼 길의 몫은 0이고 그 사이는 곧게 줄어든다.
 * maxPaths: 한 무리가 나눠 가는 길의 최대 수. penalty·attempts: 다른 길을 찾을 때 이미 찾은 길에 곱하는 값과 찾아보는 횟수.
 */
export const ALTERNATIVES = { tolerance: 0.05, maxPaths: 3, penalty: 1.08, attempts: 5 } as const;

export interface PathOption {
  path: FoundPath;
  /** 이 길로 가는 사람의 몫 (합 1) */
  share: number;
}

/**
 * from에서 targets까지 사람들이 나눠 가는 길들. 못 가면 빈 배열.
 * widthOf는 길의 지금 유효 폭(좁힌 길은 남은 폭)을 준다.
 */
export function pathOptions(
  graph: WalkGraph,
  from: string,
  targets: Iterable<string>,
  closed: ReadonlySet<string> = new Set(),
  widthOf: (edge: WalkEdge) => number = (e) => e.widthM,
): PathOption[] {
  const goal = [...targets];
  const best = findPath(graph, from, goal, closed);
  if (!best) return [];
  const ix = indexOf(graph);
  const paths = [best];
  const weight = new Map<string, number>();
  let last = best;
  for (let i = 0; i < ALTERNATIVES.attempts && paths.length < ALTERNATIVES.maxPaths; i++) {
    for (const id of last.edgeIds) weight.set(id, (weight.get(id) ?? 1) * ALTERNATIVES.penalty);
    const next = findPath(graph, from, goal, closed, weight);
    if (!next) break;
    last = next;
    const known = paths.some((p) => p.edgeIds.length === next.edgeIds.length && p.edgeIds.every((id, k) => id === next.edgeIds[k]));
    if (!known && next.cost <= best.cost * (1 + ALTERNATIVES.tolerance)) paths.push(next);
  }
  if (paths.length === 1) return [{ path: best, share: 1 }];

  // 몫 = 서로 다른 구간의 가장 좁은 폭 × (더 먼 만큼 줄어드는 값)
  const uses = new Map<string, number>();
  for (const p of paths) for (const id of p.edgeIds) uses.set(id, (uses.get(id) ?? 0) + 1);
  const weights = paths.map((p) => {
    let narrowest = Infinity;
    for (const id of p.edgeIds) if (uses.get(id)! < paths.length) narrowest = Math.min(narrowest, widthOf(graph.edges[ix.edgeIndex.get(id)!]!));
    const extra = best.cost > 0 ? (p.cost - best.cost) / (best.cost * ALTERNATIVES.tolerance) : 0;
    return (Number.isFinite(narrowest) ? Math.max(0, narrowest) : 0) * Math.max(0, 1 - extra);
  });
  const total = weights.reduce((x, y) => x + y, 0);
  if (!(total > 0)) return [{ path: best, share: 1 }];
  // 몫이 아주 작은 길은 버리고 나머지에 나눈다
  const kept = paths.map((path, i) => ({ path, share: weights[i]! / total })).filter((o) => o.share >= 0.02);
  const sum = kept.reduce((x, o) => x + o.share, 0);
  return kept.map((o) => ({ path: o.path, share: o.share / sum }));
}

// ---------- 계산 ----------

/** 길 토막 1분 통과 인원 = 유효 폭 × 폭 1m당 통과 인원 (계단은 평지의 stairsFactor배) */
export function walkCapacityPerMin(edge: Pick<WalkEdge, 'widthM' | 'kind'>, a: Assumptions): number {
  const f = a.flowPerMeterPerMin;
  return Math.max(0, edge.widthM) * (edge.kind === 'stairs' ? f.flat * f.stairsFactor : f.flat);
}

/** 길 토막의 넓이 (㎡). 아주 짧은 토막도 2m 길이는 있다고 본다 */
const areaOf = (edge: WalkEdge, widthM: number) => widthM * Math.max(2, edge.lengthM);

/**
 * 위험 판정에 쓰는 넓이의 최소 길이 (m, 가정).
 * 줄은 정체 밀도까지 붙어 선다고 계산하므로 몇 m짜리 토막은 몇 명만 서도 꽉 찬다. 그것을 위험으로 보지 않도록
 * 폭 × 이 길이에 선 사람으로 밀도를 본다. 긴 토막은 제 넓이 그대로다.
 */
export const DANGER_STRETCH_M = 10;

/** 토막 위에 설 수 있는 사람 수: 넓이 ÷ 정체 시 1인당 면적 */
function storageOf(edge: WalkEdge, widthM: number, a: Assumptions): number {
  return Math.max(4, areaOf(edge, widthM) / a.jamAreaPerPersonM2);
}

interface Step {
  /** 자원 번호: 길 토막 또는 목적지 한도 */
  r: number;
  /** 길 토막을 a→b로 가면 0, 반대면 1. 목적지 한도는 0 */
  dir: 0 | 1;
  /** 이 토막을 지난 뒤 다음 입구에 닿기까지의 분 */
  delay: number;
}

interface Group {
  route: WalkRoute;
  steps: Step[];
  depart: Float64Array;
}

const STATUS_RANK: Record<Status, number> = { 여유: 0, 보통: 1, 병목: 2, 위험: 3 };
const EPS = 1e-9;
/** 1분 안에 0분 거리 토막을 잇달아 지나는 흐름을 따라가는 횟수의 상한 */
const MAX_SWEEPS = 60;

export function simulateWalk(input: WalkInput): WalkResult {
  const { graph, assumptions: a } = input;
  const H = input.horizonMinutes ?? 120;
  const controls = input.controls ?? NO_CONTROLS;
  const compliance = Math.min(1, Math.max(0, controls.compliance));
  const weather = input.weather ? (a.weatherFactor[input.weather] ?? 1) : 1;
  const warnings: string[] = [];
  const ix = indexOf(graph);
  const edgeById = (id: string) => graph.edges[ix.edgeIndex.get(id)!]!;

  const blocked = new Set(controls.blocked.filter((id) => ix.edgeIndex.has(id)));
  const staffAt = new Map(controls.staffed.filter((s) => ix.edgeIndex.has(s.edgeId) && !blocked.has(s.edgeId)).map((s) => [s.edgeId, s]));
  // 안내를 따르는 사람은 막힌 길과 안전요원이 선 길을 모두 피한다
  const avoided = new Set([...blocked, ...staffAt.keys()]);
  const exitById = new Map(graph.exits.map((x) => [x.id, x]));
  const destById = new Map(graph.destinations.map((d) => [d.id, d]));
  const speed = a.walkSpeedMPerMin;

  // ---------- 길 찾기 ----------
  const fractions = egressFractions(input.egress, H);
  const groups: Group[] = [];
  const stranded: { tripId: string; people: number }[] = [];
  const widthOf = (e: WalkEdge) => (controls.narrowed[e.id] !== undefined ? Math.max(0, controls.narrowed[e.id]!) : e.widthM);
  const found = new Map<string, FoundPath | null>();
  const pathFor = (from: string, dest: WalkDestination, closed: ReadonlySet<string>, key: string) => {
    const k = `${key}|${from}|${dest.id}`;
    if (!found.has(k)) found.set(k, findPath(graph, from, dest.nodeIds, closed));
    return found.get(k)!;
  };
  const options = new Map<string, PathOption[]>();
  const optionsFor = (from: string, dest: WalkDestination) => {
    const k = `${from}|${dest.id}`;
    if (!options.has(k)) options.set(k, pathOptions(graph, from, dest.nodeIds, blocked, widthOf));
    return options.get(k)!;
  };
  const resources: { edge: WalkEdge | null; sinkId: string | null; cap: number; storage: number }[] = graph.edges.map((e) => ({
    edge: e,
    sinkId: null,
    cap: blocked.has(e.id) ? 0 : walkCapacityPerMin({ widthM: widthOf(e), kind: e.kind }, a) * weather,
    storage: storageOf(e, widthOf(e), a),
  }));
  const sinkResource = new Map<string, number>();
  for (const [id, sink] of Object.entries(input.sinks ?? {})) {
    if (!destById.has(id) || !(sink.capacityPerMin > 0)) continue;
    sinkResource.set(id, resources.length);
    resources.push({ edge: null, sinkId: id, cap: sink.capacityPerMin, storage: Infinity });
  }

  const stepsOf = (path: { edgeIds: string[]; forward: boolean[] }, destinationId: string): { steps: Step[]; minutes: number; lengthM: number } => {
    const steps: Step[] = [];
    let walked = 0;
    let lengthM = 0;
    path.edgeIds.forEach((id, i) => {
      const e = edgeById(id);
      const before = Math.round(walked);
      walked += e.lengthM / speed;
      lengthM += e.lengthM;
      // 토막마다 반올림하면 짧은 토막이 이어질 때 걷는 시간이 사라진다. 누적 시간을 반올림해 차이만큼 늦춘다
      steps.push({ r: ix.edgeIndex.get(id)!, dir: path.forward[i] ? 0 : 1, delay: Math.round(walked) - before });
    });
    const sink = sinkResource.get(destinationId);
    if (sink !== undefined) steps.push({ r: sink, dir: 0, delay: 0 });
    return { steps, minutes: walked, lengthM };
  };

  let guided = 0;
  let detourMinutesTotal = 0;
  for (const trip of input.trips) {
    if (!(trip.people > 0)) continue;
    const exit = exitById.get(trip.exitId);
    const dest = destById.get(trip.destinationId);
    if (!exit || !dest) {
      stranded.push({ tripId: trip.id, people: trip.people });
      continue;
    }
    const ways = optionsFor(exit.nodeId, dest);
    if (!ways.length) {
      stranded.push({ tripId: trip.id, people: trip.people });
      continue;
    }
    const start = Math.max(0, Math.round(trip.startMinute ?? 0));
    const departOf = (people: number) => {
      const d = new Float64Array(H);
      for (let t = start; t < H; t++) d[t] = fractions[t - start]! * people;
      return d;
    };

    // 거의 같은 길이 여럿이면 몫대로 나눠 간다
    for (const way of ways) {
      const base = way.path;
      const here = trip.people * way.share;
      const plain = stepsOf(base, dest.id);

      // 가는 길에 처음 만나는 안전요원. 역 안내는 지하철 이용객에게만 해당한다
      let follow: { steps: Step[]; minutes: number; lengthM: number; edgeIds: string[]; destinationId: string } | null = null;
      if (compliance > 0) {
        const at = base.edgeIds.findIndex((id) => {
          const s = staffAt.get(id);
          // 역 안내는 지하철 이용객에게만, 그리고 다른 역으로 가던 사람에게만 해당한다
          return !!s && (!s.sendTo || (trip.mode === 'rail' && s.sendTo !== dest.id));
        });
        if (at >= 0) {
          const staff = staffAt.get(base.edgeIds[at]!)!;
          const target = staff.sendTo && destById.get(staff.sendTo)?.mode === 'rail' ? destById.get(staff.sendTo)! : dest;
          const e = edgeById(base.edgeIds[at]!);
          const from = base.forward[at] ? e.a : e.b;
          const rest = pathFor(from, target, avoided, 'guided');
          if (rest) {
            const joined = { edgeIds: [...base.edgeIds.slice(0, at), ...rest.edgeIds], forward: [...base.forward.slice(0, at), ...rest.forward] };
            follow = { ...stepsOf(joined, target.id), edgeIds: joined.edgeIds, destinationId: target.id };
          } else if (!warnings.some((w) => w.startsWith(`${e.id}의 안내`) && w.includes(trip.id))) {
            warnings.push(`${e.id}의 안내를 따를 다른 길이 없어 ${trip.id}는 그대로 갑니다`);
          }
        }
      }
      const followers = follow ? here * compliance : 0;
      const others = here - followers;
      if (others > 0)
        groups.push({
          route: { tripId: trip.id, kind: 'base', people: others, destinationId: dest.id, edgeIds: base.edgeIds, walkMinutes: plain.minutes, lengthM: plain.lengthM },
          steps: plain.steps,
          depart: departOf(others),
        });
      if (follow && followers > 0) {
        guided += followers;
        detourMinutesTotal += followers * Math.max(0, follow.minutes - plain.minutes);
        groups.push({
          route: { tripId: trip.id, kind: 'guided', people: followers, destinationId: follow.destinationId, edgeIds: follow.edgeIds, walkMinutes: follow.minutes, lengthM: follow.lengthM },
          steps: follow.steps,
          depart: departOf(followers),
        });
      }
    }
  }
  for (const s of stranded) warnings.push(`${s.tripId}: 목적지까지 갈 길이 없습니다 (${Math.round(s.people)}명)`);

  // ---------- 1분씩 진행 ----------
  const R = resources.length;
  const members: { g: number; k: number }[][] = Array.from({ length: R }, () => []);
  groups.forEach((group, g) => group.steps.forEach((step, k) => members[step.r]!.push({ g, k })));
  const active = members.map((m, r) => (m.length ? r : -1)).filter((r) => r >= 0);
  // waiting[g][k] = k번째 토막에 들어가려고 기다리는 사람. 몸은 그 앞 토막 위에 있다
  const waiting = groups.map((group) => new Float64Array(group.steps.length));
  // due[g][k][t] = t분에 k번째 입구에 닿을 사람. 마지막 칸(k = 토막 수)은 목적지에 닿을 사람
  const due = groups.map((group) => Array.from({ length: group.steps.length + 1 }, () => new Float64Array(H)));
  // 방향별로 따로 센다 (마주 오는 줄이 서로의 자리를 막아 멈춰 서지 않게)
  // standing[dir][r] = r에 들어가려고 기다리는 사람 (몸은 앞 토막 위), waitOn[dir][r] = r 위에 서서 다음 토막을 기다리는 사람
  const standing = [new Float64Array(R), new Float64Array(R)];
  const waitOn = [new Float64Array(R), new Float64Array(R)];
  // r 위를 걷는 사람. 계산은 걷는 시간을 1분 단위로 늦추므로(delay) 그동안 사람을 (실제 걷는 분 ÷ delay)만큼만 센다.
  // 그러면 한 사람이 토막 위에 있는 사람·분은 실제 걷는 시간과 같다. 같은 분에 지나가는(delay 0) 사람은 그 분에만 센다
  const crossing = resources.map((res) => (res.edge ? res.edge.lengthM / speed : 0));
  const transit = [new Float64Array(R), new Float64Array(R)];
  const passing = [new Float64Array(R), new Float64Array(R)];
  const walkersOn = (dir: 0 | 1, r: number) => transit[dir]![r]! + passing[dir]![r]! * crossing[r]!;
  // r 위를 걷는 중인 사람의 1분당 흐름: 들어선 사람 ÷ 걷는 분 수(delay)를 걷는 동안 더해 둔다
  const moving = new Float64Array(R);
  const arrivals = Array.from({ length: R }, () => new Float64Array(H));
  const passed = Array.from({ length: R }, () => new Float64Array(H));
  const flow = Array.from({ length: R }, () => new Float64Array(H));
  const queue = Array.from({ length: R }, () => new Float64Array(H));
  const occupancy = Array.from({ length: R }, () => new Float64Array(H));
  const left = new Float64Array(R);
  const ledger = { departed: zeros(H), arrived: zeros(H), inTransit: zeros(H), queued: zeros(H) };
  const arrivedBy: Record<string, number> = {};
  let departedCum = 0;
  let arrivedCum = 0;
  let walking = 0;
  const landing = new Float64Array(H + 1);

  /** g 무리의 k번째 입구에 사람이 닿는다. 마지막 토막까지 지났으면 도착이다 */
  const enter = (g: number, k: number, people: number, t: number) => {
    const steps = groups[g]!.steps;
    if (k >= steps.length) {
      arrivedCum += people;
      const id = groups[g]!.route.destinationId;
      arrivedBy[id] = (arrivedBy[id] ?? 0) + people;
      return;
    }
    const s = steps[k]!;
    waiting[g]![k]! += people;
    standing[s.dir]![s.r]! += people;
    arrivals[s.r]![t]! += people;
    const before = steps[k - 1];
    if (before) waitOn[before.dir]![before.r]! += people;
  };

  for (let t = 0; t < H; t++) {
    walking -= landing[t]!;
    groups.forEach((group, g) => {
      const out = group.depart[t]!;
      if (out > 0) {
        departedCum += out;
        enter(g, 0, out, t);
      }
      for (let k = 1; k <= group.steps.length; k++) {
        const v = due[g]![k]![t]!;
        if (!(v > 0)) continue;
        // 앞 토막을 다 걸었다
        const walked = group.steps[k - 1]!;
        transit[walked.dir]![walked.r]! -= (v * crossing[walked.r]!) / walked.delay;
        moving[walked.r]! -= v / walked.delay;
        enter(g, k, v, t);
      }
    });
    for (const r of active) left[r] = resources[r]!.cap;

    // 0분 거리 토막을 지난 사람은 같은 분에 다음 입구에 닿는다. 더 움직일 사람이 없을 때까지 되풀이한다
    for (let sweep = 0; sweep < MAX_SWEEPS; sweep++) {
      let moved = false;
      for (const r of active) {
        if (left[r]! <= EPS) continue;
        const list = members[r]!;
        let want = 0;
        for (const { g, k } of list) want += waiting[g]![k]!;
        if (want <= EPS) continue;
        const share = Math.min(1, left[r]! / want);
        for (const { g, k } of list) {
          const here = waiting[g]![k]!;
          if (here <= EPS) continue;
          const steps = groups[g]!.steps;
          const step = steps[k]!;
          // 줄 번짐: 들어갈 토막 위에 설 자리가 남은 만큼만 들어간다
          const room = resources[r]!.storage - waitOn[step.dir]![r]! - walkersOn(step.dir, r);
          const go = Math.min(here * share, Math.max(0, room));
          if (go <= EPS) continue;
          waiting[g]![k] = here - go > EPS ? here - go : 0;
          standing[step.dir]![r]! -= go;
          if (step.delay === 0) passing[step.dir]![r]! += go;
          else {
            transit[step.dir]![r]! += (go * crossing[r]!) / step.delay;
            moving[r]! += go / step.delay;
          }
          const before = steps[k - 1];
          if (before) waitOn[before.dir]![before.r]! -= go;
          left[r]! -= go;
          passed[r]![t]! += go;
          moved = true;
          if (step.delay === 0) enter(g, k + 1, go, t);
          else {
            // 걷는 동안은 줄도 도착도 아니다. 계산 범위를 넘어 닿는 사람은 끝까지 걷는 중으로 남는다
            walking += go;
            if (t + step.delay < H) {
              landing[t + step.delay]! += go;
              due[g]![k + 1]![t + step.delay]! += go;
            }
          }
        }
      }
      if (!moved) break;
    }

    let standingNow = 0;
    for (const r of active) {
      const q = Math.max(0, standing[0]![r]! + standing[1]![r]!);
      queue[r]![t] = q > 1e-6 ? q : 0;
      standingNow += queue[r]![t]!;
      const o = Math.max(0, waitOn[0]![r]! + waitOn[1]![r]! + walkersOn(0, r) + walkersOn(1, r));
      occupancy[r]![t] = o > 1e-6 ? o : 0;
      const f = passing[0]![r]! + passing[1]![r]! + moving[r]!;
      // 저녁 전체를 더한 값이 지나간 사람 수와 맞도록 아주 작은 값도 남긴다 (계산 오차만 버린다)
      flow[r]![t] = f > EPS ? f : 0;
    }
    passing[0]!.fill(0);
    passing[1]!.fill(0);
    ledger.departed[t] = departedCum;
    ledger.arrived[t] = arrivedCum;
    ledger.inTransit[t] = walking;
    ledger.queued[t] = standingNow;
  }

  // ---------- 결과 ----------
  const edges: Record<string, WalkEdgeSeries> = {};
  const sinks: Record<string, WalkSinkSeries> = {};
  for (const r of active) {
    const res = resources[r]!;
    const q = Array.from(queue[r]!);
    const peakQueue = Math.max(0, ...q);
    if (res.sinkId) {
      sinks[res.sinkId] = { destinationId: res.sinkId, capacityPerMin: res.cap, queue: q, passed: Array.from(passed[r]!), peakQueue };
      continue;
    }
    const e = res.edge!;
    const area = widthOf(e) * Math.max(DANGER_STRETCH_M, e.lengthM);
    const occ = Array.from(occupancy[r]!);
    const saturation: number[] = [];
    const status: Status[] = [];
    let worst: Status = '여유';
    for (let t = 0; t < H; t++) {
      const sat = res.cap > 0 ? arrivals[r]![t]! / res.cap : arrivals[r]![t]! > 0 ? Infinity : 0;
      // 위험: 길 위의 사람이 정체 밀도에 가깝다. 병목·보통: 들어오려는 사람이 통과 용량에 견줘 많다
      const st = statusOf({ saturation: sat, queue: occ[t]!, areaPerPersonM2: occ[t]! > 0 ? area / occ[t]! : null, jamAreaPerPersonM2: a.jamAreaPerPersonM2 });
      saturation.push(sat);
      status.push(st);
      if (STATUS_RANK[st] > STATUS_RANK[worst]) worst = st;
    }
    edges[e.id] = {
      edgeId: e.id,
      capacityPerMin: res.cap,
      arrivals: Array.from(arrivals[r]!),
      passed: Array.from(passed[r]!),
      flow: Array.from(flow[r]!),
      queue: q,
      occupancy: occ,
      saturation,
      status,
      people: passed[r]!.reduce((x, y) => x + y, 0),
      peakQueue,
      worst,
    };
  }

  const queueTotal = ledger.queued.slice();
  let peakQueueTotal = 0;
  let peakIndex = -1;
  let lastQueued = -1;
  queueTotal.forEach((q, t) => {
    if (q > peakQueueTotal + EPS) {
      peakQueueTotal = q;
      peakIndex = t;
    }
    if (q > 1e-6) lastQueued = t;
  });
  const clearMinute = lastQueued === -1 ? 0 : lastQueued === H - 1 ? null : lastQueued + 2;
  if (clearMinute === null) warnings.push(`계산 범위 ${H}분 안에 줄이 다 풀리지 않습니다`);
  const people = groups.reduce((s, g) => s + g.route.people, 0);
  const strandedPeople = stranded.reduce((s, x) => s + x.people, 0);

  return {
    minutes: H,
    edges,
    sinks,
    queueTotal,
    routes: groups.map((g) => g.route),
    totals: {
      people,
      stranded: strandedPeople,
      peakQueueTotal,
      peakQueueTotalMinute: peakIndex + 1,
      queueMinutesTotal: queueTotal.reduce((x, y) => x + y, 0),
      detourMinutesTotal,
      clearMinute,
      guided,
      meanWalkMinutes: people > 0 ? groups.reduce((s, g) => s + g.route.people * g.route.walkMinutes, 0) / people : 0,
    },
    arrivedBy,
    stranded,
    ledger,
    warnings,
  };
}

const zeros = (n: number) => Array<number>(n).fill(0);
