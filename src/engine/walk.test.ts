import { describe, expect, it } from 'vitest';
import walkways from '../data/walkways.json';
import { bundle } from '../data';
import { ALTERNATIVES, findPath, pathOptions, simulateWalk, walkCapacityPerMin, NO_CONTROLS, type WalkEdge, type WalkGraph, type WalkInput, type WalkTrip } from './walk';
import { tenMinutesFlat, testAssumptions } from './testing/fixtures';

/**
 * 작은 공원. 걷는 속도 분당 70m, 폭 1m당 분당 100명.
 *
 *   X ──a── J ──b── D1       b는 폭 2m 병목 (분당 200명)
 *           ├──c── K ──d── D1 로 돌아가는 넓은 길 (140m + 140m)
 *           └──e── D2        700m 떨어진 다른 역
 */
const edge = (id: string, a: string, b: string, lengthM: number, extra: Partial<WalkEdge> = {}): WalkEdge => ({
  id,
  a,
  b,
  lengthM,
  widthM: 100,
  widthSource: '가정',
  kind: 'walk',
  cost: 1,
  ...extra,
});

function park(edges: WalkEdge[] = []): WalkGraph {
  return {
    schemaVersion: 1,
    source: 'test',
    nodes: ['X', 'J', 'K', 'D1', 'D2'].map((id) => ({ id, lngLat: [127.12, 37.52] })),
    edges: edges.length
      ? edges
      : [edge('a', 'X', 'J', 70), edge('b', 'J', 'D1', 70, { widthM: 2 }), edge('c', 'J', 'K', 140), edge('d', 'K', 'D1', 140), edge('e', 'J', 'D2', 700)],
    exits: [{ id: 'exit', name: '출구', nodeId: 'X' }],
    destinations: [
      { id: 'station-1', mode: 'rail', name: '가까운 역', lngLat: [127.12, 37.52], nodeIds: ['D1'], direction: 'A' },
      { id: 'station-2', mode: 'rail', name: '먼 역', lngLat: [127.12, 37.52], nodeIds: ['D2'], direction: 'B' },
      { id: 'stop-1', mode: 'bus', name: '정류장', lngLat: [127.12, 37.52], nodeIds: ['D1'] },
    ],
  };
}

const trip = (people: number, destinationId = 'station-1', extra: Partial<WalkTrip> = {}): WalkTrip => ({
  id: `to-${destinationId}`,
  exitId: 'exit',
  destinationId,
  mode: destinationId.startsWith('stop') ? 'bus' : 'rail',
  people,
  ...extra,
});

const input = (trips: WalkTrip[], extra: Partial<WalkInput> = {}): WalkInput => ({
  graph: park(),
  trips,
  egress: tenMinutesFlat,
  assumptions: testAssumptions,
  horizonMinutes: 60,
  ...extra,
});

const balanced = (r: ReturnType<typeof simulateWalk>) =>
  r.ledger.departed.every((d, t) => Math.abs(d - (r.ledger.arrived[t]! + r.ledger.inTransit[t]! + r.ledger.queued[t]!)) < 1e-6);

describe('길 찾기', () => {
  const graph = park();

  it('가장 수월한 길로 간다', () => {
    const p = findPath(graph, 'X', ['D1'])!;
    expect(p.edgeIds).toEqual(['a', 'b']);
    expect(p.forward).toEqual([true, true]);
    expect(p.lengthM).toBe(140);
    expect(p.nodeId).toBe('D1');
  });

  it('막힌 길은 돌아간다', () => {
    expect(findPath(graph, 'X', ['D1'], new Set(['b']))!.edgeIds).toEqual(['a', 'c', 'd']);
    expect(findPath(graph, 'X', ['D1'], new Set(['a']))).toBeNull();
  });

  it('길 종류별 비용이 크면 더 긴 길을 고른다', () => {
    const costly = park([edge('a', 'X', 'J', 70), edge('b', 'J', 'D1', 70, { cost: 5 }), edge('c', 'J', 'K', 140), edge('d', 'K', 'D1', 140)]);
    // b는 70m × 5 = 350, 돌아가는 길은 280
    expect(findPath(costly, 'X', ['D1'])!.edgeIds).toEqual(['a', 'c', 'd']);
  });

  it('여러 입구 가운데 가장 가까운 곳으로 가고, 거꾸로 걷는 토막을 표시한다', () => {
    expect(findPath(graph, 'X', ['D2', 'D1'])!.nodeId).toBe('D1');
    const back = findPath(graph, 'D1', ['X'])!;
    expect(back.edgeIds).toEqual(['b', 'a']);
    expect(back.forward).toEqual([false, false]);
    expect(findPath(graph, 'X', ['없는 점'])).toBeNull();
  });
});

describe('거의 같은 길로 나눠 가기', () => {
  /** X ──a── J 에서 D1까지 나란한 두 길: b는 100m에 폭 3m, c는 102m에 폭 9m */
  const lanes = (cLength = 102, extra: WalkEdge[] = []) =>
    park([edge('a', 'X', 'J', 70), edge('b', 'J', 'D1', 100, { widthM: 3 }), edge('c', 'J', 'D1', cLength, { widthM: 9 }), ...extra]);
  const shareOf = (options: ReturnType<typeof pathOptions>, edgeId: string) => options.find((o) => o.path.edgeIds.includes(edgeId))?.share ?? 0;

  it('길이가 거의 같으면 폭에 비례해 나누고, 더 먼 길은 그만큼 덜 간다', () => {
    const options = pathOptions(lanes(), 'X', ['D1']);
    expect(options.map((o) => o.path.edgeIds)).toEqual([
      ['a', 'b'],
      ['a', 'c'],
    ]);
    // c는 2m(170m의 1.18%) 더 멀다. 허용 5%의 23.5%를 썼으므로 몫이 그만큼 줄어든다
    const taper = 1 - 2 / (170 * ALTERNATIVES.tolerance);
    expect(shareOf(options, 'b')).toBeCloseTo(3 / (3 + 9 * taper), 9);
    expect(shareOf(options, 'b') + shareOf(options, 'c')).toBeCloseTo(1, 9);
  });

  it('길이와 폭이 같으면 반씩 간다', () => {
    const even = park([edge('a', 'X', 'J', 70), edge('b', 'J', 'D1', 100, { widthM: 4 }), edge('c', 'J', 'D1', 100, { widthM: 4 })]);
    expect(pathOptions(even, 'X', ['D1']).map((o) => o.share)).toEqual([0.5, 0.5]);
  });

  it('허용 범위보다 더 먼 길은 쓰지 않는다', () => {
    // 170m의 5%는 8.5m. c가 9m 더 멀면 아무도 가지 않는다
    const options = pathOptions(lanes(109), 'X', ['D1']);
    expect(options).toHaveLength(1);
    expect(options[0]!.path.edgeIds).toEqual(['a', 'b']);
    expect(options[0]!.share).toBe(1);
  });

  it('한쪽을 좁히면 다른 쪽으로 더 가고, 막으면 모두 다른 쪽으로 간다', () => {
    const graph = lanes();
    const before = shareOf(pathOptions(graph, 'X', ['D1']), 'b');
    const narrowed = pathOptions(graph, 'X', ['D1'], new Set(), (e) => (e.id === 'b' ? 1 : e.widthM));
    expect(shareOf(narrowed, 'b')).toBeLessThan(before / 2);
    const closed = pathOptions(graph, 'X', ['D1'], new Set(['b']));
    expect(closed.map((o) => [o.path.edgeIds, o.share])).toEqual([[['a', 'c'], 1]]);
    expect(pathOptions(graph, 'X', ['D1'], new Set(['a']))).toEqual([]);
  });

  it('나눠 가는 길은 셋까지', () => {
    const many = lanes(100, [edge('c2', 'J', 'D1', 100, { widthM: 9 }), edge('c3', 'J', 'D1', 100, { widthM: 9 }), edge('c4', 'J', 'D1', 100, { widthM: 9 })]);
    const options = pathOptions(many, 'X', ['D1']);
    expect(options).toHaveLength(ALTERNATIVES.maxPaths);
    expect(options.reduce((s, o) => s + o.share, 0)).toBeCloseTo(1, 9);
  });

  it('계산에서도 그 몫대로 길을 지나고, 좁히면 몫이 옮겨 간다', () => {
    const graph = lanes();
    const r = simulateWalk(input([trip(4000)], { graph }));
    const share = shareOf(pathOptions(graph, 'X', ['D1']), 'b');
    expect(r.edges.b!.people).toBeCloseTo(4000 * share, 6);
    expect(r.edges.c!.people).toBeCloseTo(4000 * (1 - share), 6);
    expect(r.routes.map((x) => [x.kind, x.edgeIds.join('')])).toEqual([
      ['base', 'ab'],
      ['base', 'ac'],
    ]);
    expect(balanced(r)).toBe(true);
    const narrowed = simulateWalk(input([trip(4000)], { graph, controls: { ...NO_CONTROLS, narrowed: { b: 1 } } }));
    expect(narrowed.edges.b!.people).toBeLessThan(r.edges.b!.people / 2);
    expect(narrowed.edges.b!.people + narrowed.edges.c!.people).toBeCloseTo(4000, 6);
  });

  it('나란한 길 한쪽에 안전요원을 세우면 따르는 사람만 다른 쪽으로 옮겨 간다', () => {
    const graph = lanes();
    const share = shareOf(pathOptions(graph, 'X', ['D1']), 'b');
    const r = simulateWalk(input([trip(4000)], { graph, controls: { ...NO_CONTROLS, staffed: [{ edgeId: 'b' }], compliance: 0.5 } }));
    expect(r.totals.guided).toBeCloseTo(4000 * share * 0.5, 6);
    expect(r.edges.b!.people).toBeCloseTo(4000 * share * 0.5, 6);
    expect(r.edges.c!.people).toBeCloseTo(4000 - 4000 * share * 0.5, 6);
  });
});

describe('보행 흐름', () => {
  it('10분간 분당 400명이 분당 200명 병목으로 가면 줄이 2,000명까지 선다', () => {
    const r = simulateWalk(input([trip(4000)]));
    expect(walkCapacityPerMin({ widthM: 2, kind: 'walk' }, testAssumptions)).toBe(200);
    expect(r.edges.b!.capacityPerMin).toBe(200);
    expect(r.totals.people).toBe(4000);
    expect(r.totals.peakQueueTotal).toBeCloseTo(2000, 6);
    // 1분 걸어 병목에 닿고, 10분 동안 쌓인 뒤 10분 동안 풀린다
    expect(r.totals.peakQueueTotalMinute).toBe(11);
    expect(r.totals.clearMinute).toBe(21);
    // 병목에 들어가려는 사람이 통과 용량의 두 배다. 기다리는 사람은 넓은 앞길에 서 있어 밀도는 낮다
    expect(r.edges.b!.worst).toBe('병목');
    expect(r.edges.b!.peakQueue).toBeCloseTo(2000, 6);
    // 앞길 위에는 기다리는 사람과 걷는 사람이 함께 있다: 9분째에 대기 1,800명 + 막 들어온 400명
    expect(Math.max(...r.edges.a!.occupancy)).toBeCloseTo(2200, 6);
    expect(r.edges.a!.occupancy[10]).toBeCloseTo(2000, 6);
    expect(r.edges.b!.people).toBeCloseTo(4000, 6);
    expect(r.arrivedBy['station-1']).toBeCloseTo(4000, 6);
    expect(balanced(r)).toBe(true);
  });

  it('길 위가 꽉 차면 그 길로 더 들어오지 못하고 줄이 뒤로 번진다', () => {
    // X ─a─ J ─b(폭 2m)─ M ─c(폭 1m)─ D1: c 앞에서 못 간 사람이 b 위에 서고, b가 차면 a에서 기다린다
    const tight: WalkGraph = {
      ...park(),
      nodes: ['X', 'J', 'M', 'D1'].map((id) => ({ id, lngLat: [127.12, 37.52] })),
      edges: [edge('a', 'X', 'J', 70), edge('b', 'J', 'M', 70, { widthM: 2 }), edge('c', 'M', 'D1', 70, { widthM: 1 })],
    };
    const r = simulateWalk(input([trip(4000)], { graph: tight }));
    // b 위에 설 수 있는 사람: 폭 2m × 70m ÷ 1인당 면적. 서서 기다리는 사람과 걷는 사람이 그만큼 찬다
    const room = (2 * 70) / testAssumptions.jamAreaPerPersonM2;
    expect(Math.max(...r.edges.b!.occupancy)).toBeLessThanOrEqual(room + 1e-6);
    expect(Math.max(...r.edges.b!.occupancy)).toBeGreaterThan(room * 0.97);
    expect(r.edges.b!.worst).toBe('위험');
    // b가 찬 뒤로는 c가 받는 분당 100명만큼만 b에 들어간다
    expect(r.edges.b!.passed[15]).toBeCloseTo(100, 6);
    expect(Math.max(...r.edges.a!.occupancy)).toBeGreaterThan(2000);
    expect(balanced(r)).toBe(true);
  });

  it('짧은 토막 위의 사람은 1분이 아니라 실제로 걷는 시간만큼만 센다', () => {
    // X ─a(70m)─ J ─s(7m, 폭 2.5m)─ K ─d(63m)─ D1. 분당 400명이 7m를 6초에 지난다: 토막 위에는 약 40명
    const short: WalkGraph = {
      ...park(),
      nodes: ['X', 'J', 'K', 'D1'].map((id) => ({ id, lngLat: [127.12, 37.52] })),
      edges: [edge('a', 'X', 'J', 70), edge('s', 'J', 'K', 7, { widthM: 5 }), edge('d', 'K', 'D1', 63)],
    };
    const r = simulateWalk(input([trip(4000)], { graph: short }));
    expect(Math.max(...r.edges.s!.occupancy)).toBeCloseTo(400 * (7 / 70), 6);
    expect(r.edges.s!.worst).not.toBe('위험');
    expect(r.edges.s!.peakQueue).toBe(0);
    expect(balanced(r)).toBe(true);
  });

  it('걷는 시간만큼 늦게 닿는다', () => {
    const r = simulateWalk(input([trip(100, 'station-2')], { egress: { kind: 'custom', fractions: [1] } }));
    // a 1분 + e 10분
    expect(r.ledger.arrived[10]).toBe(0);
    expect(r.ledger.arrived[11]).toBeCloseTo(100, 6);
    expect(r.routes[0]!.walkMinutes).toBeCloseTo(11, 9);
    expect(r.totals.meanWalkMinutes).toBeCloseTo(11, 9);
  });

  it('짧은 토막이 이어져도 걷는 시간이 사라지지 않는다', () => {
    // 28m 토막 10개: 하나씩 반올림하면 0분이지만 합치면 4분
    const nodes = Array.from({ length: 11 }, (_, i) => `P${i}`);
    const chain: WalkGraph = {
      ...park(),
      nodes: nodes.map((id) => ({ id, lngLat: [127.12, 37.52] })),
      edges: nodes.slice(1).map((id, i) => edge(`s${i}`, nodes[i]!, id, 28)),
      exits: [{ id: 'exit', name: '출구', nodeId: 'P0' }],
      destinations: [{ id: 'station-1', mode: 'rail', name: '역', lngLat: [127.12, 37.52], nodeIds: ['P10'] }],
    };
    const r = simulateWalk({ graph: chain, trips: [trip(100)], egress: { kind: 'custom', fractions: [1] }, assumptions: testAssumptions, horizonMinutes: 20 });
    expect(r.ledger.arrived[3]).toBe(0);
    expect(r.ledger.arrived[4]).toBeCloseTo(100, 6);
    expect(balanced(r)).toBe(true);
  });

  it('마주 오는 두 흐름은 한 길의 용량을 나눠 쓴다', () => {
    const two: WalkGraph = {
      ...park(),
      nodes: ['L', 'R'].map((id) => ({ id, lngLat: [127.12, 37.52] })),
      edges: [edge('n', 'L', 'R', 70, { widthM: 2 })],
      exits: [
        { id: 'left', name: '왼쪽 출구', nodeId: 'L' },
        { id: 'right', name: '오른쪽 출구', nodeId: 'R' },
      ],
      destinations: [
        { id: 'east', mode: 'bus', name: '동쪽', lngLat: [127.12, 37.52], nodeIds: ['R'] },
        { id: 'west', mode: 'bus', name: '서쪽', lngLat: [127.12, 37.52], nodeIds: ['L'] },
      ],
    };
    const trips: WalkTrip[] = [
      { id: 'eastbound', exitId: 'left', destinationId: 'east', mode: 'bus', people: 3000 },
      { id: 'westbound', exitId: 'right', destinationId: 'west', mode: 'bus', people: 1000 },
    ];
    const r = simulateWalk({ graph: two, trips, egress: tenMinutesFlat, assumptions: testAssumptions, horizonMinutes: 60 });
    // 분당 300명 + 100명이 들어오고 200명만 지난다. 요구 비율대로 150명과 50명
    expect(r.edges.n!.passed[0]).toBeCloseTo(200, 6);
    expect(r.edges.n!.arrivals[0]).toBeCloseTo(400, 6);
    expect(r.arrivedBy.east! + r.arrivedBy.west!).toBeCloseTo(4000, 6);
    expect(balanced(r)).toBe(true);
  });

  it('목적지가 받는 한도를 넘으면 그 앞에 줄이 선다', () => {
    const wide = park([edge('a', 'X', 'J', 70), edge('b', 'J', 'D1', 70)]);
    const r = simulateWalk(input([trip(4000)], { graph: wide, sinks: { 'station-1': { capacityPerMin: 300, source: '관측' } } }));
    expect(Object.values(r.edges).every((e) => e.peakQueue === 0 || e.edgeId === 'b')).toBe(true);
    expect(r.sinks['station-1']!.capacityPerMin).toBe(300);
    // 분당 400명이 닿고 300명이 들어간다: 10분 뒤 1,000명
    expect(r.totals.peakQueueTotal).toBeCloseTo(1000, 6);
    expect(r.arrivedBy['station-1']).toBeCloseTo(4000, 6);
    expect(balanced(r)).toBe(true);
  });
});

describe('운영자가 놓은 것', () => {
  it('길을 막으면 돌아가고, 막힌 길에는 아무도 없다', () => {
    const r = simulateWalk(input([trip(4000)], { controls: { ...NO_CONTROLS, blocked: ['b'] } }));
    expect(r.routes[0]!.edgeIds).toEqual(['a', 'c', 'd']);
    expect(r.routes[0]!.walkMinutes).toBeCloseTo(5, 9);
    expect(r.edges.b).toBeUndefined();
    expect(r.totals.peakQueueTotal).toBe(0);
    expect(r.arrivedBy['station-1']).toBeCloseTo(4000, 6);
  });

  it('갈 길이 모두 막히면 그 사람들은 계산에서 빠지고 따로 알린다', () => {
    const r = simulateWalk(input([trip(4000), trip(500, 'station-2')], { controls: { ...NO_CONTROLS, blocked: ['b', 'c'] } }));
    expect(r.stranded).toEqual([{ tripId: 'to-station-1', people: 4000 }]);
    expect(r.totals.stranded).toBe(4000);
    expect(r.totals.people).toBe(500);
    expect(r.warnings.some((w) => w.includes('갈 길이 없습니다'))).toBe(true);
  });

  it('길을 좁히면 그 폭만큼만 지난다', () => {
    const r = simulateWalk(input([trip(4000)], { controls: { ...NO_CONTROLS, narrowed: { b: 1 } } }));
    expect(r.edges.b!.capacityPerMin).toBe(100);
    expect(r.totals.peakQueueTotal).toBeCloseTo(3000, 6);
  });

  it('안내요원을 세우면 반응률만큼 그 길 앞에서 돌아간다', () => {
    const r = simulateWalk(input([trip(4000)], { controls: { ...NO_CONTROLS, staffed: [{ edgeId: 'b' }], compliance: 0.6 } }));
    const guided = r.routes.find((x) => x.kind === 'guided')!;
    const base = r.routes.find((x) => x.kind === 'base')!;
    expect(guided.people).toBeCloseTo(2400, 6);
    expect(guided.edgeIds).toEqual(['a', 'c', 'd']);
    expect(base.people).toBeCloseTo(1600, 6);
    expect(base.edgeIds).toEqual(['a', 'b']);
    expect(r.totals.guided).toBeCloseTo(2400, 6);
    // 병목에는 분당 160명만 가서 줄이 없다. 돌아간 사람은 3분씩 더 걷는다
    expect(r.totals.peakQueueTotal).toBe(0);
    expect(r.totals.detourMinutesTotal).toBeCloseTo(2400 * 3, 6);
    expect(balanced(r)).toBe(true);
  });

  it('다른 역으로 안내하면 지하철 이용객만 그 역으로 간다', () => {
    const controls = { ...NO_CONTROLS, staffed: [{ edgeId: 'b', sendTo: 'station-2' }], compliance: 0.5 };
    const r = simulateWalk(input([trip(4000), trip(1000, 'stop-1')], { controls }));
    expect(r.arrivedBy['station-2']).toBeCloseTo(2000, 6);
    expect(r.arrivedBy['station-1']).toBeCloseTo(2000, 6);
    // 버스 타러 가는 사람은 역 안내와 상관없이 그대로 간다
    expect(r.arrivedBy['stop-1']).toBeCloseTo(1000, 6);
    expect(r.routes.filter((x) => x.kind === 'guided').map((x) => [x.tripId, x.destinationId])).toEqual([['to-station-1', 'station-2']]);
  });

  it('이미 그 역으로 가는 사람은 안내와 상관없다', () => {
    const controls = { ...NO_CONTROLS, staffed: [{ edgeId: 'a', sendTo: 'station-2' }], compliance: 1 };
    const r = simulateWalk(input([trip(1000, 'station-2')], { controls }));
    expect(r.routes.map((x) => x.kind)).toEqual(['base']);
    expect(r.totals.guided).toBe(0);
  });

  it('반응률이 0이면 안내요원을 세워도 아무도 길을 바꾸지 않는다', () => {
    const r = simulateWalk(input([trip(4000)], { controls: { ...NO_CONTROLS, staffed: [{ edgeId: 'b' }], compliance: 0 } }));
    expect(r.routes.map((x) => x.kind)).toEqual(['base']);
    expect(r.totals.peakQueueTotal).toBeCloseTo(2000, 6);
  });

  it('돌아갈 길이 없으면 그대로 가고 그 사실을 알린다', () => {
    const only = park([edge('a', 'X', 'J', 70), edge('b', 'J', 'D1', 70, { widthM: 2 })]);
    const r = simulateWalk(input([trip(4000)], { graph: only, controls: { ...NO_CONTROLS, staffed: [{ edgeId: 'b' }], compliance: 0.6 } }));
    expect(r.routes.map((x) => x.kind)).toEqual(['base']);
    expect(r.warnings.some((w) => w.includes('다른 길이 없어'))).toBe(true);
  });
});

describe('올림픽공원 보행로 (src/data/walkways.json)', () => {
  const graph = walkways as unknown as WalkGraph;
  const station = (id: string) => graph.destinations.find((d) => d.id === id)!;
  const dome = graph.exits.filter((x) => x.venueId === 'kspo-dome');

  it('모든 길의 끝점이 그래프에 있고 폭과 길이가 양수다', () => {
    const nodes = new Set(graph.nodes.map((n) => n.id));
    expect(graph.edges.length).toBeGreaterThan(1500);
    for (const e of graph.edges) {
      expect(nodes.has(e.a) && nodes.has(e.b)).toBe(true);
      expect(e.lengthM).toBeGreaterThan(0);
      expect(e.widthM).toBeGreaterThan(0);
      expect(e.cost).toBeGreaterThan(0);
    }
    expect(new Set(graph.edges.map((e) => e.id)).size).toBe(graph.edges.length);
  });

  it('KSPO DOME 세 출구에서 세 역으로 갈 수 있다', () => {
    expect(dome.map((x) => x.id).sort()).toEqual(['n-kspo-exit-east', 'n-kspo-exit-south', 'n-kspo-exit-west']);
    for (const exit of dome)
      for (const id of ['n-dest-a', 'n-dest-b', 'n-dest-c']) {
        const p = findPath(graph, exit.nodeId, station(id).nodeIds)!;
        expect(p).not.toBeNull();
        expect(p.lengthM).toBeGreaterThan(300);
        expect(p.lengthM).toBeLessThan(2500);
      }
  });

  it('다른 공연장은 가정한 출구 하나씩을 갖고 세 역에 닿는다', () => {
    const others = graph.exits.filter((x) => x.venueId !== 'kspo-dome');
    expect(others.map((x) => x.venueId).sort()).toEqual(['olympic-hall', 'ticketlink-live-arena', 'woori-art-hall']);
    for (const exit of others) expect(findPath(graph, exit.nodeId, station('n-dest-a').nodeIds)).not.toBeNull();
  });

  it('올림픽공원역까지의 길이는 첫 길 데이터의 경로와 비슷하다', () => {
    // 첫 길 데이터의 A 방면: 남측 출구에서 727m
    const first = ['e-01', 'e-02', 'e-03', 'e-04', 'e-station-a'].reduce((s, id) => s + bundle.network.edges.find((e) => e.id === id)!.lengthM, 0);
    const south = graph.exits.find((x) => x.id === 'n-kspo-exit-south')!;
    const p = findPath(graph, south.nodeId, station('n-dest-a').nodeIds)!;
    expect(Math.abs(p.lengthM - first) / first).toBeLessThan(0.25);
  });

  it('지하철 이용객 4,211명을 흘려 보내면 사람 수가 맞고 한 번 계산이 빨리 끝난다', () => {
    const trips: WalkTrip[] = dome.flatMap((exit) =>
      (['n-dest-a', 'n-dest-b', 'n-dest-c'] as const).map((id, i) => ({
        id: `${exit.id}>${id}`,
        exitId: exit.id,
        destinationId: id,
        mode: 'rail' as const,
        people: (4211 / 3) * [0.985, 0.006, 0.009][i]!,
      })),
    );
    const started = performance.now();
    const r = simulateWalk({ graph, trips, egress: { kind: 'gamma', totalMinutes: 22, peakMinute: 6 }, assumptions: bundle.assumptions, horizonMinutes: 120 });
    const took = performance.now() - started;
    expect(r.totals.people).toBeCloseTo(4211, 6);
    expect(r.stranded).toEqual([]);
    expect(balanced(r)).toBe(true);
    expect(r.ledger.arrived[119]! + r.ledger.inTransit[119]! + r.ledger.queued[119]!).toBeCloseTo(4211, 3);
    expect(r.ledger.arrived[119]).toBeGreaterThan(4000);
    expect(r.totals.meanWalkMinutes).toBeGreaterThan(6);
    expect(r.totals.meanWalkMinutes).toBeLessThan(25);
    expect(took).toBeLessThan(1500);
  });
});
