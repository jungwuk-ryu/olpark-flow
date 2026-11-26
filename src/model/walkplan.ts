import type { DataBundle, EventItem, Prediction } from '../data/contract';
import {
  simulateWalk,
  type LngLat,
  type Status,
  type WalkControls,
  type WalkEdge,
  type WalkEdgeSeries,
  type WalkExit,
  type WalkGraph,
  type WalkMode,
  type WalkResult,
  type WalkSink,
  type WalkTrip,
} from '../engine';
import type { ScenarioParams } from './scenario';

/**
 * 공연 하나를 보행로 시뮬레이션에 올린다: 예측에서 "어느 출구에서 어디로 몇 명"을 만들고,
 * 운영자가 지도에 놓은 것(길 막기·장애물·안전요원)을 반영해 계산한다.
 *
 * 지도에 올리는 사람은 지하철 이용객(역 출입구)과 타는 정류장을 아는 버스 이용객이다.
 * 차량 이용객은 올리지 못한다: 주차장(P1, P2, P4~P7)의 보행 출입구 좌표가 없다.
 */

export type OffMapMode = WalkMode | 'walk' | 'other';

/** 지도에 올리지 못한 사람과 그 까닭 */
export interface OffMap {
  mode: OffMapMode;
  people: number;
  /** no-place = 예측이 갈 곳을 정하지 못함, not-on-map = 갈 곳은 있으나 보행로 그래프에 위치가 없음 (주차장) */
  reason: 'no-place' | 'not-on-map';
}

export interface WalkDemand {
  trips: WalkTrip[];
  /** 지도에 올린 사람 (수단별) */
  onMap: Record<WalkMode, number>;
  offMap: OffMap[];
  exits: WalkExit[];
  /** 출구 위치가 실측이 아니라 건물에서 가장 가까운 보행로로 둔 가정인지 */
  assumedExits: boolean;
}

/**
 * 공연의 보행 수요. 이 공연장의 출구가 그래프에 없으면 null.
 * 화면의 조건(관객 수, 지하철 이용 비율)을 바꾸면 예측의 인원을 같은 비율로 옮긴다.
 */
export function walkDemand(graph: WalkGraph, event: EventItem, p: Prediction, params: ScenarioParams): WalkDemand | null {
  const exits = graph.exits.filter((x) => x.venueId === event.venueId);
  if (!exits.length) return null;
  const shareSum = exits.reduce((s, x) => s + (x.share ?? 1), 0);
  const places = new Map(graph.destinations.map((d) => [d.id, d]));
  const crowd = p.attendance && p.attendance.value > 0 ? params.attendance / p.attendance.value : 1;
  const railShare = Math.max(0, Math.min(params.stationShare, 1 - params.vehicleRatio));

  // 예측이 준 "수단 → 갈 곳" 인원. 없으면(목업) 지하철 이용객을 방면 비율로 세 역에 나눈다
  let wanted: { mode: WalkMode; destinationId: string; people: number }[];
  const offMap: OffMap[] = [];
  const walks = p.brief?.walks;
  if (walks) {
    const rail = p.rail && p.rail.ratio > 0 ? railShare / p.rail.ratio : 1;
    wanted = walks.places.map((w) => ({ ...w, people: w.people * crowd * (w.mode === 'rail' ? rail : 1) }));
    for (const [mode, people] of Object.entries(walks.unplaced) as [OffMapMode, number][])
      if (people > 0) offMap.push({ mode, people: people * crowd, reason: 'no-place' });
  } else {
    const riders = params.attendance * railShare;
    wanted = Object.entries(p.directionShare ?? { A: 1 }).flatMap(([direction, share]) => {
      const station = graph.destinations.find((d) => d.mode === 'rail' && d.direction === direction);
      return station && share ? [{ mode: 'rail' as const, destinationId: station.id, people: riders * share }] : [];
    });
    // 목업 예측에는 수단별 갈 곳이 없다. 차량과 나머지는 지도에 올리지 못한 사람으로 남긴다
    const cars = params.attendance * Math.min(params.vehicleRatio, 1 - railShare);
    if (cars > 0) offMap.push({ mode: 'car', people: cars, reason: 'not-on-map' });
    const rest = params.attendance - riders - cars;
    if (rest > 0.5) offMap.push({ mode: 'other', people: rest, reason: 'no-place' });
  }

  const trips: WalkTrip[] = [];
  const onMap: Record<WalkMode, number> = { rail: 0, bus: 0, car: 0 };
  for (const w of wanted) {
    if (!(w.people > 0)) continue;
    if (!places.has(w.destinationId)) {
      offMap.push({ mode: w.mode, people: w.people, reason: 'not-on-map' });
      continue;
    }
    onMap[w.mode] += w.people;
    for (const exit of exits)
      trips.push({ id: `${exit.id}>${w.destinationId}`, exitId: exit.id, destinationId: w.destinationId, mode: w.mode, people: (w.people * (exit.share ?? 1)) / shareSum });
  }
  // 같은 수단·같은 까닭은 한 줄로 모은다
  const merged = new Map<string, OffMap>();
  for (const o of offMap) {
    const key = `${o.mode}|${o.reason}`;
    merged.set(key, { ...o, people: (merged.get(key)?.people ?? 0) + o.people });
  }
  return { trips, onMap, offMap: [...merged.values()], exits, assumedExits: exits.some((x) => (x as WalkExit & { assumed?: boolean }).assumed) };
}

/**
 * 역이 1분에 받아들이는 인원.
 * 올림픽공원역은 개찰구가 실제로 통과시킨 인원(관측)을 쓰고, 그 값이 없거나 다른 역이면 출입구 계단 폭 3m의 통과 용량(가정)을 쓴다.
 */
export function walkSinks(graph: WalkGraph, data: DataBundle): Record<string, WalkSink> {
  const f = data.assumptions.flowPerMeterPerMin;
  const stairs = 3 * f.flat * f.stairsFactor;
  const observed = data.evidence?.gates?.capacityPer5Min;
  const sinks: Record<string, WalkSink> = {};
  for (const d of graph.destinations) {
    if (d.mode !== 'rail') continue;
    sinks[d.id] = d.direction === 'A' && observed ? { capacityPerMin: observed / 5, source: '관측' } : { capacityPerMin: stairs, source: '가정' };
  }
  return sinks;
}

export interface WalkRun {
  demand: WalkDemand;
  sinks: Record<string, WalkSink>;
  /** 아무것도 놓지 않았을 때 */
  baseline: WalkResult;
  /** 지도에 놓은 것을 반영했을 때. 놓은 것이 없으면 baseline과 같은 객체 */
  current: WalkResult;
}

export const hasControls = (c: WalkControls) => c.blocked.length > 0 || c.staffed.length > 0 || Object.keys(c.narrowed).length > 0;

/** 공연 하나를 계산한다. 이 공연장의 출구가 그래프에 없으면 null */
export function runWalk(data: DataBundle, graph: WalkGraph, event: EventItem, p: Prediction, params: ScenarioParams, controls: WalkControls): WalkRun | null {
  const demand = walkDemand(graph, event, p, params);
  if (!demand) return null;
  const sinks = walkSinks(graph, data);
  const peak = p.egressCurve ? p.egressCurve.peakMinute / p.egressCurve.totalMinutes : 0.27;
  const egress = { kind: 'gamma' as const, totalMinutes: params.egressTotalMinutes, peakMinute: Math.min(params.egressTotalMinutes - 1, Math.max(1, Math.round(params.egressTotalMinutes * peak))) };
  const base = { graph, trips: demand.trips, egress, assumptions: data.assumptions, sinks, horizonMinutes: 120 };
  const baseline = simulateWalk(base);
  const current = hasControls(controls) ? simulateWalk({ ...base, controls: { ...controls, compliance: params.compliance } }) : baseline;
  return { demand, sinks, baseline, current };
}

// ---------- 지도에 놓는 것 ----------

/** 길 하나에는 한 가지만 놓는다: 놓으려는 것 말고는 그 길에서 치운다 */
function cleared(c: WalkControls, edgeId: string): WalkControls {
  const narrowed = { ...c.narrowed };
  delete narrowed[edgeId];
  return { ...c, blocked: c.blocked.filter((id) => id !== edgeId), narrowed, staffed: c.staffed.filter((s) => s.edgeId !== edgeId) };
}

export const removeControl = cleared;

export function blockEdge(c: WalkControls, edgeId: string): WalkControls {
  const next = cleared(c, edgeId);
  return { ...next, blocked: [...next.blocked, edgeId] };
}

export function narrowEdge(c: WalkControls, edgeId: string, widthM: number): WalkControls {
  const next = cleared(c, edgeId);
  return { ...next, narrowed: { ...next.narrowed, [edgeId]: Math.max(0.5, Math.round(widthM * 10) / 10) } };
}

export function staffEdge(c: WalkControls, edgeId: string, sendTo?: string): WalkControls {
  const next = cleared(c, edgeId);
  return { ...next, staffed: [...next.staffed, sendTo ? { edgeId, sendTo } : { edgeId }] };
}

export type ControlKind = 'block' | 'narrow' | 'staff';

/** 그 길에 놓인 것 */
export function controlAt(c: WalkControls, edgeId: string): ControlKind | null {
  if (c.blocked.includes(edgeId)) return 'block';
  if (c.narrowed[edgeId] !== undefined) return 'narrow';
  if (c.staffed.some((s) => s.edgeId === edgeId)) return 'staff';
  return null;
}

// ---------- 지도와 표에 쓰는 값 ----------

const edgeLine = (e: WalkEdge, nodeAt: Map<string, LngLat>): LngLat[] => e.path ?? [nodeAt.get(e.a)!, nodeAt.get(e.b)!];

/** 길 토막의 가운데 점 (꺾인 선을 따라 절반 지점) */
export function edgeMidpoint(line: LngLat[]): LngLat {
  if (line.length < 2) return line[0]!;
  const lengths = line.slice(1).map((p, i) => Math.hypot((p[0] - line[i]![0]) * Math.cos((p[1] * Math.PI) / 180), p[1] - line[i]![1]));
  let half = lengths.reduce((x, y) => x + y, 0) / 2;
  for (let i = 0; i < lengths.length; i++) {
    if (half <= lengths[i]! || i === lengths.length - 1) {
      const t = lengths[i]! > 0 ? Math.min(1, half / lengths[i]!) : 0;
      return [line[i]![0] + (line[i + 1]![0] - line[i]![0]) * t, line[i]![1] + (line[i + 1]![1] - line[i]![1]) * t];
    }
    half -= lengths[i]!;
  }
  return line[line.length - 1]!;
}

const metres = (a: LngLat, b: LngLat) => Math.hypot((a[0] - b[0]) * Math.cos((a[1] * Math.PI) / 180) * 111320, (a[1] - b[1]) * 110540);

export interface EdgeGeometry {
  line: Map<string, LngLat[]>;
  mid: Map<string, LngLat>;
}

/** 길마다 그릴 선과 가운데 점. 그래프가 같으면 한 번만 만든다 */
const geometries = new WeakMap<WalkGraph, EdgeGeometry>();
export function edgeGeometry(graph: WalkGraph): EdgeGeometry {
  let g = geometries.get(graph);
  if (!g) {
    const nodeAt = new Map(graph.nodes.map((n) => [n.id, n.lngLat]));
    const line = new Map(graph.edges.map((e) => [e.id, edgeLine(e, nodeAt)]));
    g = { line, mid: new Map([...line].map(([id, l]) => [id, edgeMidpoint(l)])) };
    geometries.set(graph, g);
  }
  return g;
}

/** 길 이름이 없으므로 가장 가까운 출구·역·정류장·주차장으로 위치를 말한다 */
export function nearestPlace(graph: WalkGraph, at: LngLat): { name: string; metres: number } | null {
  const nodeAt = new Map(graph.nodes.map((n) => [n.id, n.lngLat]));
  let best: { name: string; metres: number } | null = null;
  const consider = (name: string, where: LngLat | undefined) => {
    if (!where) return;
    const d = metres(at, where);
    if (!best || d < best.metres) best = { name, metres: d };
  };
  for (const x of graph.exits) consider(x.name, nodeAt.get(x.nodeId));
  for (const d of graph.destinations) consider(d.name.replace(/\s*\(\d+\)$/, ''), d.lngLat);
  return best;
}

export function placeText(place: { name: string; metres: number } | null): string {
  if (!place) return '위치 미상';
  return place.metres < 60 ? `${place.name} 앞` : `${place.name}에서 ${Math.round(place.metres / 10) * 10}m`;
}

const RANK: Record<Status, number> = { 여유: 0, 보통: 1, 병목: 2, 위험: 3 };

export interface BusyEdge {
  edge: WalkEdge;
  series: WalkEdgeSeries;
  where: string;
  mid: LngLat;
  /** 가장 많이 몰린 1분에 이 길로 들어오려던 사람과 그 시각 (종료 후 분) */
  peakInflow: number;
  peakMinute: number;
}

/** 저녁 전체에서 가장 붐빈 길부터. 상태가 나쁜 순, 같으면 기다린 사람이 많은 순, 그다음 지나간 사람이 많은 순 */
export function busiestEdges(graph: WalkGraph, result: WalkResult, count = 8): BusyEdge[] {
  const byId = new Map(graph.edges.map((e) => [e.id, e]));
  const geometry = edgeGeometry(graph);
  const rows = Object.values(result.edges)
    .filter((s) => s.worst !== '여유')
    .sort((a, b) => RANK[b.worst] - RANK[a.worst] || b.peakQueue - a.peakQueue || b.people - a.people)
    .map((series) => {
      const edge = byId.get(series.edgeId)!;
      const mid = geometry.mid.get(edge.id)!;
      let peakInflow = 0;
      let peakIndex = 0;
      series.arrivals.forEach((v, t) => {
        if (v > peakInflow) {
          peakInflow = v;
          peakIndex = t;
        }
      });
      return { edge, series, where: placeText(nearestPlace(graph, mid)), mid, peakInflow, peakMinute: peakIndex + 1 };
    });
  // 한 길이 여러 토막으로 나뉘어 같은 자리·같은 상태·같은 인원으로 되풀이되면 한 줄만 남긴다
  const shown: BusyEdge[] = [];
  for (const row of rows) {
    const twin = shown.some(
      (s) => s.where === row.where && s.edge.kind === row.edge.kind && s.series.worst === row.series.worst && Math.round(s.peakInflow) === Math.round(row.peakInflow) && Math.round(s.series.peakQueue) === Math.round(row.series.peakQueue),
    );
    if (!twin) shown.push(row);
    if (shown.length >= count) break;
  }
  return shown;
}

/** 길 종류의 이름 */
export const KIND_NAME: Record<WalkEdge['kind'], string> = {
  plaza: '광장·보행자 도로',
  walk: '보행로',
  sidewalk: '보도',
  crossing: '횡단보도',
  stairs: '계단',
  trail: '산책로',
  road: '차도 옆길',
  aisle: '주차장 통로',
};
