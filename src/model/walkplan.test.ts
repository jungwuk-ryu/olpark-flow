import { describe, expect, it } from 'vitest';
import walkways from '../data/walkways.json';
import sample from '../api/testing/server-bundle.sample.json';
import { fromServer, type ServerBundle } from '../api';
import { bundle } from '../data';
import { NO_CONTROLS, type WalkGraph } from '../engine';
import { defaultParams } from './scenario';
import {
  blockEdge,
  busiestEdges,
  controlAt,
  edgeGeometry,
  edgeMidpoint,
  hasControls,
  narrowEdge,
  nearestPlace,
  placeText,
  removeControl,
  runWalk,
  staffEdge,
  walkDemand,
  walkSinks,
} from './walkplan';

const graph = walkways as unknown as WalkGraph;
/** 서버 응답 예시: 11/27(금) KSPO DOME 공연 (관객 9,956명, 지하철 4,211명) */
const data = fromServer(sample as unknown as ServerBundle);
const dome = data.events.events.find((e) => e.id === 'kspo-20261127-1900')!;
const domeP = data.predictions.predictions.find((p) => p.eventId === dome.id)!;
const hall = data.events.events.find((e) => e.venueId === 'olympic-hall')!;
const hallP = data.predictions.predictions.find((p) => p.eventId === hall.id)!;
const sum = (xs: number[]) => xs.reduce((a, b) => a + b, 0);

describe('공연의 보행 수요', () => {
  const demand = walkDemand(graph, dome, domeP, defaultParams(domeP))!;

  it('예측의 수단별 인원을 세 출구에 나눠 무리로 만든다', () => {
    expect(demand.exits.map((x) => x.id).sort()).toEqual(['n-kspo-exit-east', 'n-kspo-exit-south', 'n-kspo-exit-west']);
    expect(demand.assumedExits).toBe(false);
    // 지하철 이용객은 세 역으로: 예측의 역·호선별 인원 합
    const lines = domeP.brief!.stationLines!;
    expect(demand.onMap.rail).toBeCloseTo(sum(lines.map((l) => l.people)), 6);
    const toA = demand.trips.filter((t) => t.destinationId === 'n-dest-a');
    expect(toA).toHaveLength(3);
    expect(sum(toA.map((t) => t.people))).toBeCloseTo(sum(lines.filter((l) => l.direction === 'A').map((l) => l.people)), 6);
    expect(toA.every((t) => t.mode === 'rail')).toBe(true);
  });

  it('지도에 올린 사람과 올리지 못한 사람을 더하면 관객 수다', () => {
    const on = demand.onMap.rail + demand.onMap.bus + demand.onMap.car;
    const off = sum(demand.offMap.map((o) => o.people));
    expect(sum(demand.trips.map((t) => t.people))).toBeCloseTo(on, 6);
    // 수단별 인원을 정수로 반올림한 차이만 남는다
    expect(Math.abs(on + off - domeP.attendance!.value)).toBeLessThan(15);
  });

  it('갈 곳이 없거나 지도에 없는 사람은 까닭과 함께 따로 센다', () => {
    const by = (mode: string, reason: string) => demand.offMap.find((o) => o.mode === mode && o.reason === reason)?.people ?? 0;
    // 걸어서 귀가하는 사람과 픽업은 예측이 갈 곳을 정하지 못한다
    expect(by('walk', 'no-place')).toBe(domeP.brief!.walks!.unplaced.walk);
    expect(by('other', 'no-place')).toBe(domeP.brief!.walks!.unplaced.other);
    // 주차장은 보행 출입구 좌표가 없어 그래프에 없다. 예측이 주차장을 정한 사람도 지도에 올리지 못한다
    expect(graph.destinations.filter((d) => d.mode === 'car')).toEqual([]);
    const parked = sum(domeP.brief!.walks!.places.filter((w) => w.mode === 'car').map((w) => w.people));
    expect(parked).toBeGreaterThan(0);
    expect(by('car', 'not-on-map')).toBeCloseTo(parked, 6);
    expect(by('car', 'no-place')).toBe(domeP.brief!.walks!.unplaced.car);
    expect(demand.onMap.car).toBe(0);
  });

  it('관객 수와 지하철 이용 비율을 바꾸면 같은 비율로 옮긴다', () => {
    const base = defaultParams(domeP);
    const more = walkDemand(graph, dome, domeP, { ...base, attendance: base.attendance * 2 })!;
    expect(more.onMap.rail).toBeCloseTo(demand.onMap.rail * 2, 6);
    expect(more.onMap.bus).toBeCloseTo(demand.onMap.bus * 2, 6);
    const fewerRiders = walkDemand(graph, dome, domeP, { ...base, stationShare: base.stationShare / 2 })!;
    expect(fewerRiders.onMap.rail).toBeCloseTo(demand.onMap.rail / 2, 6);
    expect(fewerRiders.onMap.bus).toBeCloseTo(demand.onMap.bus, 6);
  });

  it('출구 위치를 가정한 공연장은 그렇게 표시하고, 그래프에 없는 공연장은 계산하지 않는다', () => {
    const d = walkDemand(graph, hall, hallP, defaultParams(hallP))!;
    expect(d.exits.map((x) => x.id)).toEqual(['x-olympic-hall']);
    expect(d.assumedExits).toBe(true);
    expect(d.onMap.rail).toBeGreaterThan(1000);
    expect(walkDemand(graph, { ...dome, venueId: 'nowhere' as never }, domeP, defaultParams(domeP))).toBeNull();
  });

  it('서버 예측이 없으면(목업) 지하철 이용객을 방면 비율로 세 역에 나눈다', () => {
    const event = bundle.events.events.find((e) => e.id === 'kspo-20261121-a')!;
    const p = bundle.predictions.predictions.find((x) => x.eventId === event.id)!;
    const d = walkDemand(graph, event, p, defaultParams(p))!;
    // 관객 7,860명 중 차량 18%를 뺀 82%
    expect(d.onMap.rail).toBeCloseTo(7860 * 0.82, 6);
    expect(d.onMap.bus + d.onMap.car).toBe(0);
    expect(sum(d.trips.filter((t) => t.destinationId === 'n-dest-a').map((t) => t.people))).toBeCloseTo(7860 * 0.82 * 0.72, 6);
    // 차량 이용객은 지도에 올리지 못한 사람으로 남는다
    expect(d.offMap).toHaveLength(1);
    expect([d.offMap[0]!.mode, d.offMap[0]!.reason]).toEqual(['car', 'not-on-map']);
    expect(d.offMap[0]!.people).toBeCloseTo(7860 * 0.18, 6);
  });

  it('목업에서 지하철 이용 비율을 낮추면 남는 사람도 올리지 못한 사람으로 센다', () => {
    const event = bundle.events.events.find((e) => e.id === 'kspo-20261121-a')!;
    const p = bundle.predictions.predictions.find((x) => x.eventId === event.id)!;
    const d = walkDemand(graph, event, p, { ...defaultParams(p), stationShare: 0.5 })!;
    expect(d.onMap.rail).toBeCloseTo(7860 * 0.5, 6);
    expect(sum(d.offMap.map((o) => o.people)) + d.onMap.rail).toBeCloseTo(7860, 6);
    expect(d.offMap.find((o) => o.mode === 'other')!.people).toBeCloseTo(7860 * 0.32, 6);
  });
});

describe('역이 받는 한도', () => {
  it('올림픽공원역은 개찰구 관측값, 다른 역은 계단 폭 가정을 쓴다', () => {
    const sinks = walkSinks(graph, data);
    expect(sinks['n-dest-a']).toEqual({ capacityPerMin: data.evidence!.gates!.capacityPer5Min! / 5, source: '관측' });
    const f = data.assumptions.flowPerMeterPerMin;
    expect(sinks['n-dest-b']).toEqual({ capacityPerMin: 3 * f.flat * f.stairsFactor, source: '가정' });
    expect(Object.keys(sinks).sort()).toEqual(['n-dest-a', 'n-dest-b', 'n-dest-c']);
  });

  it('관측값이 없으면 올림픽공원역도 가정을 쓴다', () => {
    expect(walkSinks(graph, bundle)['n-dest-a']!.source).toBe('가정');
  });
});

describe('지도에 놓는 것', () => {
  it('길 하나에는 한 가지만 놓인다', () => {
    let c = blockEdge(NO_CONTROLS, 'e1');
    expect(controlAt(c, 'e1')).toBe('block');
    c = narrowEdge(c, 'e1', 1.26);
    expect([controlAt(c, 'e1'), c.blocked, c.narrowed]).toEqual(['narrow', [], { e1: 1.3 }]);
    c = staffEdge(c, 'e1', 'n-dest-b');
    expect([controlAt(c, 'e1'), c.narrowed, c.staffed]).toEqual(['staff', {}, [{ edgeId: 'e1', sendTo: 'n-dest-b' }]]);
    c = staffEdge(c, 'e1');
    expect(c.staffed).toEqual([{ edgeId: 'e1' }]);
    c = removeControl(c, 'e1');
    expect([controlAt(c, 'e1'), hasControls(c)]).toEqual([null, false]);
  });

  it('좁힌 폭은 0.5m보다 작아지지 않는다', () => {
    expect(narrowEdge(NO_CONTROLS, 'e1', 0.1).narrowed.e1).toBe(0.5);
    expect(hasControls(narrowEdge(NO_CONTROLS, 'e1', 2))).toBe(true);
  });
});

describe('공연 계산', () => {
  const params = defaultParams(domeP);
  const run = runWalk(data, graph, dome, domeP, params, NO_CONTROLS)!;

  it('아무것도 놓지 않으면 한 번만 계산하고 사람 수가 맞는다', () => {
    expect(run.current).toBe(run.baseline);
    expect(run.baseline.totals.people).toBeCloseTo(run.demand.onMap.rail + run.demand.onMap.bus + run.demand.onMap.car, 6);
    expect(run.baseline.stranded).toEqual([]);
    const L = run.baseline.ledger;
    expect(L.arrived[119]! + L.inTransit[119]! + L.queued[119]!).toBeCloseTo(run.baseline.totals.people, 3);
    expect(run.baseline.arrivedBy['n-dest-a']).toBeGreaterThan(3900);
  });

  it('남측 출구에서 올림픽공원역으로는 광장길과 육교길로 나눠 간다', () => {
    const south = graph.exits.find((x) => x.id === 'n-kspo-exit-south')!;
    const routes = run.baseline.routes.filter((r) => r.tripId === `${south.id}>n-dest-a`);
    expect(routes.length).toBeGreaterThan(1);
    const people = sum(routes.map((r) => r.people));
    expect(people).toBeCloseTo(run.demand.trips.find((t) => t.id === `${south.id}>n-dest-a`)!.people, 6);
    // 어느 한 길에 모두 몰리지 않는다
    for (const r of routes) expect(r.people / people).toBeLessThan(0.8);
  });

  it('올림픽공원역으로 가는 길을 막으면 다른 길로 돌아가고 계산이 달라진다', () => {
    const south = graph.exits.find((x) => x.id === 'n-kspo-exit-south')!;
    const routes = run.baseline.routes.filter((r) => r.tripId === `${south.id}>n-dest-a`);
    // 두 길이 갈라진 뒤의 토막 하나를 막는다
    const shared = new Set(routes[1]!.edgeIds);
    const own = routes[0]!.edgeIds.filter((id) => !shared.has(id));
    const middle = own[Math.floor(own.length / 2)]!;
    const blocked = runWalk(data, graph, dome, domeP, params, blockEdge(NO_CONTROLS, middle))!;
    expect(blocked.current).not.toBe(blocked.baseline);
    expect(blocked.current.edges[middle]).toBeUndefined();
    const again = blocked.current.routes.filter((r) => r.tripId === `${south.id}>n-dest-a`);
    for (const r of again) expect(r.edgeIds).not.toContain(middle);
    expect(sum(again.map((r) => r.people))).toBeCloseTo(sum(routes.map((r) => r.people)), 6);
    expect(blocked.current.arrivedBy['n-dest-a']).toBeGreaterThan(3900);
    expect(blocked.current.stranded).toEqual([]);
  });

  it('한성백제역으로 안내하면 반응률만큼 그 역으로 간다', () => {
    const south = graph.exits.find((x) => x.id === 'n-kspo-exit-south')!;
    const first = run.baseline.routes.find((r) => r.tripId === `${south.id}>n-dest-a`)!.edgeIds[0]!;
    const guided = runWalk(data, graph, dome, domeP, { ...params, compliance: 0.5 }, staffEdge(NO_CONTROLS, first, 'n-dest-b'))!;
    const before = run.baseline.arrivedBy['n-dest-b'] ?? 0;
    const moved = guided.current.totals.guided;
    expect(moved).toBeGreaterThan(300);
    expect(guided.current.arrivedBy['n-dest-b']! - before).toBeCloseTo(moved, 0);
    expect(guided.current.totals.detourMinutesTotal).toBeGreaterThan(0);
  });

  it('붐빈 길은 상태가 나쁜 순으로 나오고 위치를 가까운 곳 이름으로 말한다', () => {
    const busy = busiestEdges(graph, run.baseline, 5);
    expect(busy.length).toBeLessThanOrEqual(5);
    for (const b of busy) {
      expect(b.series.worst).not.toBe('여유');
      expect(b.where).toMatch(/앞$|m$/);
      expect(b.peakMinute).toBeGreaterThan(0);
      // 보통 이상이면 들어오려는 사람이 통과 용량의 70%를 넘은 적이 있다
      expect(b.peakInflow).toBeGreaterThanOrEqual(b.series.capacityPerMin * 0.7 - 1e-6);
    }
    const rank = { 여유: 0, 보통: 1, 병목: 2, 위험: 3 } as const;
    for (let i = 1; i < busy.length; i++) expect(rank[busy[i - 1]!.series.worst]).toBeGreaterThanOrEqual(rank[busy[i]!.series.worst]);
  });
});

describe('길의 위치', () => {
  it('꺾인 선의 가운데 점을 낸다', () => {
    const straight = edgeMidpoint([[127.12, 37.52], [127.122, 37.52]]);
    expect(straight[0]).toBeCloseTo(127.121, 9);
    expect(straight[1]).toBe(37.52);
    const bent = edgeMidpoint([[127.12, 37.52], [127.12, 37.521], [127.1213, 37.521]]);
    // 세로 111m + 가로 115m: 절반(113m)은 꺾인 뒤 2m 지점
    expect(bent[1]).toBeCloseTo(37.521, 6);
    expect(bent[0]).toBeGreaterThan(127.12);
    expect(bent[0]).toBeLessThan(127.1202);
  });

  it('모든 길에 그릴 선과 가운데 점이 있다', () => {
    const g = edgeGeometry(graph);
    expect(g.line.size).toBe(graph.edges.length);
    expect(edgeGeometry(graph)).toBe(g);
    for (const line of g.line.values()) expect(line.length).toBeGreaterThanOrEqual(2);
  });

  it('가까운 곳의 이름과 거리로 위치를 말한다', () => {
    const station = graph.destinations.find((d) => d.id === 'n-dest-a')!;
    expect(placeText(nearestPlace(graph, station.lngLat))).toBe('올림픽공원역 앞');
    expect(placeText({ name: '올림픽공원역', metres: 234 })).toBe('올림픽공원역에서 230m');
    expect(placeText(null)).toBe('위치 미상');
  });
});
