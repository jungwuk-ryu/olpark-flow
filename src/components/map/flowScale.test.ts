import { describe, expect, it } from 'vitest';
import walkways from '../../data/walkways.json';
import sample from '../../api/testing/server-bundle.sample.json';
import { fromServer, type ServerBundle } from '../../api';
import { NO_CONTROLS, simulateWalk, type WalkEdge, type WalkGraph } from '../../engine';
import { tenMinutesFlat, testAssumptions } from '../../engine/testing/fixtures';
import { defaultParams } from '../../model/scenario';
import { runWalk } from '../../model/walkplan';
import { edgeFlows, FLOW_RAMP, MINUTE_SCALE, RAMP_CSS, scaleFor, scalePosition, TOTAL_SCALE } from './flowScale';

describe('통행량 색 눈금', () => {
  it('눈금 값은 같은 간격의 색 단계에 놓이고 그 사이는 곧게 잇는다', () => {
    const stops = MINUTE_SCALE.stops;
    expect(stops).toEqual([0, 10, 50, 100, 200, 400]);
    stops.forEach((v, i) => expect(scalePosition(v, stops)).toBeCloseTo(i / 5, 12));
    // 50과 100의 가운데인 75는 두 색 단계의 가운데다
    expect(scalePosition(75, stops)).toBeCloseTo(0.5, 12);
    expect(scalePosition(5, stops)).toBeCloseTo(0.1, 12);
    expect(scalePosition(300, stops)).toBeCloseTo(0.9, 12);
    // 누적 1명은 첫 색 단계의 시작점에 가깝다
    expect(scalePosition(1, TOTAL_SCALE.stops)).toBeCloseTo(0.002, 12);
  });

  it('마지막 눈금을 넘으면 마지막 색이고, 0 이하나 숫자가 아닌 값은 첫 색이다', () => {
    expect(scalePosition(400, MINUTE_SCALE.stops)).toBe(1);
    expect(scalePosition(9999, MINUTE_SCALE.stops)).toBe(1);
    expect(scalePosition(0, MINUTE_SCALE.stops)).toBe(0);
    expect(scalePosition(-3, MINUTE_SCALE.stops)).toBe(0);
    expect(scalePosition(Number.NaN, MINUTE_SCALE.stops)).toBe(0);
  });

  it('값이 크면 눈금 위치도 크다', () => {
    for (const scale of [MINUTE_SCALE, TOTAL_SCALE]) {
      let before = -1;
      for (let v = 0; v <= scale.stops[scale.stops.length - 1]!; v += scale.stops[1]! / 4) {
        const at = scalePosition(v, scale.stops);
        expect(at).toBeGreaterThanOrEqual(before);
        if (v > 0) expect(at).toBeGreaterThan(before);
        before = at;
      }
    }
  });

  it('전체 시간대 보기와 시간별 보기는 눈금이 다르다', () => {
    expect(scaleFor(null)).toBe(TOTAL_SCALE);
    expect(scaleFor(0)).toBe(MINUTE_SCALE);
    expect(scaleFor(30)).toBe(MINUTE_SCALE);
    expect(TOTAL_SCALE.stops).toHaveLength(FLOW_RAMP.length);
    expect(MINUTE_SCALE.stops).toHaveLength(FLOW_RAMP.length);
  });

  it('범례 막대와 지도는 같은 초록·노랑·주황·빨강 눈금을 쓴다', () => {
    const luminance = (hex: string) => {
      const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
      return 0.2126 * r! + 0.7152 * g! + 0.0722 * b!;
    };
    // 첫 색은 낮은 통행량을 옅게 표시하고, 나머지 색은 흰 바탕과 2:1 넘게 구별된다
    for (const color of FLOW_RAMP.slice(1)) expect(1.05 / (luminance(color) + 0.05)).toBeGreaterThan(2);
    expect(RAMP_CSS).toBe('linear-gradient(to right, #c6d0c8 0%, #70a23e 20%, #b5ab2c 40%, #d8ad19 60%, #e47c28 80%, #ca4033 100%)');
  });
});

describe('지도에 칠할 값', () => {
  // X ─a(70m)─ J ─b(70m, 폭 2m)─ D1: 10분 동안 분당 400명이 나와 분당 200명 병목 앞에 줄을 선다
  const edge = (id: string, a: string, b: string, extra: Partial<WalkEdge> = {}): WalkEdge => ({ id, a, b, lengthM: 70, widthM: 100, widthSource: '가정', kind: 'walk', cost: 1, ...extra });
  const graph: WalkGraph = {
    schemaVersion: 1,
    source: 'test',
    nodes: ['X', 'J', 'D1'].map((id) => ({ id, lngLat: [127.12, 37.52] })),
    edges: [edge('a', 'X', 'J'), edge('b', 'J', 'D1', { widthM: 2 })],
    exits: [{ id: 'exit', name: '출구', nodeId: 'X' }],
    destinations: [{ id: 'station', mode: 'rail', name: '역', lngLat: [127.12, 37.52], nodeIds: ['D1'] }],
  };
  const result = simulateWalk({ graph, trips: [{ id: 't', exitId: 'exit', destinationId: 'station', mode: 'rail', people: 4000 }], egress: tenMinutesFlat, assumptions: testAssumptions, horizonMinutes: 60 });

  it('전체 시간대 보기는 지나간 사람 수와 가장 나빴던 혼잡도다', () => {
    const all = edgeFlows(result, null);
    expect(all.get('a')).toEqual({ value: result.edges.a!.people, status: result.edges.a!.worst });
    expect(all.get('b')!.value).toBeCloseTo(4000, 6);
    expect(all.get('b')!.status).toBe('병목');
  });

  it('시간별 보기는 그 1분의 통행량과 혼잡도다', () => {
    // 종료 +5분: a에 분당 400명, b에 분당 200명
    const five = edgeFlows(result, 5);
    expect(five.get('a')!.value).toBeCloseTo(400, 6);
    expect(five.get('b')!.value).toBeCloseTo(200, 6);
    expect(five.get('b')!.status).toBe(result.edges.b!.status[4]);
    // 종료 +0분에는 아직 아무도 나오지 않았다
    expect(edgeFlows(result, 0).size).toBe(0);
  });

  it('지나는 사람 없이 서 있기만 한 길은 통행량 0으로 칠한다', () => {
    // 종료 +15분: 나오는 사람은 끊겼고 a 위에는 b를 기다리는 줄이 남아 있다
    const later = edgeFlows(result, 15);
    expect(result.edges.a!.occupancy[14]).toBeGreaterThan(500);
    expect(later.get('a')!.value).toBe(0);
    expect(later.get('b')!.value).toBeCloseTo(200, 6);
    // 줄이 다 풀리면 칠하지 않는다
    expect(edgeFlows(result, 40).size).toBe(0);
  });

  it('계산 범위를 넘는 시점은 마지막 분으로 본다', () => {
    expect(edgeFlows(result, 999)).toEqual(edgeFlows(result, 60));
  });
});

describe('실제 보행로와 공연', () => {
  const graph = walkways as unknown as WalkGraph;
  const data = fromServer(sample as unknown as ServerBundle);
  const event = data.events.events.find((e) => e.id === 'kspo-20261127-1900')!;
  const prediction = data.predictions.predictions.find((p) => p.eventId === event.id)!;
  const run = runWalk(data, graph, event, prediction, defaultParams(prediction), NO_CONTROLS)!;

  it('칠한 값은 엔진이 계산한 값 그대로이고, 사람이 지난 길은 빠짐없이 칠한다', () => {
    const all = edgeFlows(run.current, null);
    const used = Object.values(run.current.edges).filter((s) => s.people >= 0.5);
    expect(all.size).toBe(used.length);
    expect(all.size).toBeGreaterThan(200);
    for (const s of used) expect(all.get(s.edgeId)!.value).toBe(s.people);
    for (const minute of [1, 5, 10, 20, 40]) {
      for (const [id, x] of edgeFlows(run.current, minute)) {
        const s = run.current.edges[id]!;
        expect(x.value).toBe(s.flow[minute - 1]);
        expect(x.status).toBe(s.status[minute - 1]);
        // 1분 통행량은 그 길의 통과 용량을 넘지 않는다
        expect(x.value).toBeLessThanOrEqual(s.capacityPerMin + 1e-6);
      }
    }
  });

  it('분별 통행량을 모두 더하면 전체 시간대의 값이다', () => {
    const totals = new Map<string, number>();
    for (let minute = 1; minute <= run.current.minutes; minute++) for (const [id, x] of edgeFlows(run.current, minute)) totals.set(id, (totals.get(id) ?? 0) + x.value);
    for (const [id, x] of edgeFlows(run.current, null)) expect(totals.get(id) ?? 0).toBeGreaterThan(x.value - 0.5 * run.current.minutes);
    // 가장 많이 지나는 길에서는 오차 없이 맞는다
    const [busiest] = [...edgeFlows(run.current, null)].sort((p, q) => q[1].value - p[1].value);
    expect(run.current.edges[busiest![0]]!.flow.reduce((x, y) => x + y, 0)).toBeCloseTo(busiest![1].value, 6);
  });
});
