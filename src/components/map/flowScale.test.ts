import { describe, expect, it } from 'vitest';
import walkways from '../../data/walkways.json';
import sample from '../../api/testing/server-bundle.sample.json';
import { fromServer, type ServerBundle } from '../../api';
import { NO_CONTROLS, simulateWalk, type WalkEdge, type WalkGraph } from '../../engine';
import { tenMinutesFlat, testAssumptions } from '../../engine/testing/fixtures';
import { defaultParams } from '../../model/scenario';
import { narrowEdge, runWalk } from '../../model/walkplan';
import type { Status } from '../../engine';
import { CONGESTION_RAMP, congestionPosition, edgeFlows, lineWidth, lineWidthExpression, outerWidthExpression, RAMP_CSS, STATUS_HEX } from './flowScale';

describe('혼잡도 색 눈금', () => {
  it('여유는 옅은 색에서 초록까지, 보통·병목·위험은 각자의 상태색이다', () => {
    expect(congestionPosition('여유', 0)).toBe(0);
    // 여유 안에서는 통과 용량에 견준 비율(포화도)이 보통 기준(0.7)에 가까울수록 진한 초록이다
    expect(congestionPosition('여유', 0.35)).toBeCloseTo(0.25, 12);
    expect(congestionPosition('여유', 0.69)).toBeLessThan(0.5);
    const at = (status: Status) => CONGESTION_RAMP.find((x) => x.color === STATUS_HEX[status])!.at;
    expect(congestionPosition('보통', 0.8)).toBe(at('보통'));
    expect(congestionPosition('병목', 1.4)).toBe(at('병목'));
    expect(congestionPosition('위험', 0)).toBe(1);
    expect(at('위험')).toBe(1);
  });

  it('상태가 나쁠수록 눈금 위치가 크다', () => {
    const order = [congestionPosition('여유', 0), congestionPosition('여유', 0.5), congestionPosition('여유', 5), congestionPosition('보통', 0), congestionPosition('병목', 0), congestionPosition('위험', 0)];
    for (let i = 1; i < order.length; i++) expect(order[i]).toBeGreaterThan(order[i - 1]!);
  });

  it('숫자가 아닌 포화도는 여유의 첫 색이다', () => {
    expect(congestionPosition('여유', Number.NaN)).toBe(0);
    expect(congestionPosition('여유', -1)).toBe(0);
  });

  it('여유의 가장 옅은 색도 배경 지도 위에서 보인다', () => {
    const luminance = (hex: string) => {
      const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
      return 0.2126 * r! + 0.7152 * g! + 0.0722 * b!;
    };
    // 사람이 지나는 길은 여유라도 흰 바탕과 2:1 넘게 구별된다
    expect(1.05 / (luminance(CONGESTION_RAMP[0].color) + 0.05)).toBeGreaterThan(2);
  });

  it('범례 막대와 지도는 같은 눈금을 쓴다', () => {
    expect(RAMP_CSS).toBe(`linear-gradient(to right, ${CONGESTION_RAMP.map((x) => `${x.color} ${x.at * 100}%`).join(', ')})`);
  });
});

describe('선 굵기', () => {
  const level = (status: Status) => congestionPosition(status, 0);

  it('병목·위험인 길은 기본 확대에서도 여유·보통보다 두 배 넘게 굵다', () => {
    expect(lineWidth(level('여유'), 13)).toBe(lineWidth(level('보통'), 13));
    expect(lineWidth(level('병목'), 13)).toBeGreaterThanOrEqual(2 * lineWidth(level('보통'), 13));
    expect(lineWidth(level('위험'), 13)).toBe(lineWidth(level('병목'), 13));
  });

  it('확대할수록 굵어지고, 병목·위험은 어느 배율에서나 더 굵다', () => {
    for (const status of ['여유', '병목'] as const) {
      const widths = [13, 15, 17, 19].map((zoom) => lineWidth(level(status), zoom));
      for (let i = 1; i < widths.length; i++) expect(widths[i]).toBeGreaterThan(widths[i - 1]!);
    }
    for (const zoom of [13, 15, 17, 19]) expect(lineWidth(level('병목'), zoom)).toBeGreaterThan(lineWidth(level('보통'), zoom));
  });

  it('바깥 너비는 어느 배율에서나 가장 굵은 선보다 넓다', () => {
    const expr = outerWidthExpression(2.5) as unknown as unknown[];
    expect(expr.slice(0, 3)).toEqual(['interpolate', ['linear'], ['zoom']]);
    const stops = expr.slice(3);
    for (let i = 0; i < stops.length; i += 2) expect(stops[i + 1]).toBe(lineWidth(level('위험'), stops[i] as number) + 2.5);
  });

  it('지도 표현식은 같은 굵기 규칙으로 만든다', () => {
    const expr = lineWidthExpression() as unknown as unknown[];
    expect(expr.slice(0, 3)).toEqual(['interpolate', ['linear'], ['zoom']]);
    const stops = expr.slice(3);
    for (let i = 0; i < stops.length; i += 2) {
      const zoom = stops[i] as number;
      const [, cond, wide, normal] = stops[i + 1] as [string, unknown[], number, number];
      expect(cond[0]).toBe('>=');
      expect(cond[2]).toBe(level('병목'));
      expect(wide).toBe(lineWidth(level('병목'), zoom));
      expect(normal).toBe(lineWidth(level('여유'), zoom));
    }
    // 테두리는 선보다 바깥으로 두른다
    const casing = lineWidthExpression(2.5) as unknown as unknown[];
    expect((casing[4] as number[])[3]).toBe(lineWidth(level('여유'), 13) + 2.5);
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
    expect(all.get('a')).toMatchObject({ value: result.edges.a!.people, status: result.edges.a!.worst });
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

describe('길을 좁혀 줄이 선 저녁', () => {
  // 올림픽홀 공연에서 가장 많이 지나는 길을 0.5m로 좁히면 그 뒤로 줄이 번져 사람이 서 있기만 한 길이 생긴다
  const graph = walkways as unknown as WalkGraph;
  const data = fromServer(sample as unknown as ServerBundle);
  const event = data.events.events.find((e) => e.id === 'olympichall-20261127-2000')!;
  const prediction = data.predictions.predictions.find((p) => p.eventId === event.id)!;
  const base = runWalk(data, graph, event, prediction, defaultParams(prediction), NO_CONTROLS)!;
  const busiest = Object.values(base.baseline.edges).sort((a, b) => b.people - a.people)[0]!;
  const run = runWalk(data, graph, event, prediction, defaultParams(prediction), narrowEdge(NO_CONTROLS, busiest.edgeId, 0.5))!;

  it('통행량이 0이어도 위험한 길은 위험 색으로 칠한다', () => {
    let stuck = 0;
    for (let minute = 1; minute <= run.current.minutes; minute++) {
      for (const [, x] of edgeFlows(run.current, minute)) {
        if (x.status === '위험' && x.value < 1) stuck++;
        expect(x.level).toBe(congestionPosition(x.status, x.saturation));
        if (x.status === '위험') expect(x.level).toBe(1);
      }
    }
    // 이 저녁에는 줄에 막혀 거의 움직이지 못하는 위험한 길이 실제로 있다
    expect(stuck).toBeGreaterThan(10);
  });

  it('전체 시간대 보기는 가장 나빴던 상태와 가장 높았던 포화도로 칠한다', () => {
    for (const [id, x] of edgeFlows(run.current, null)) {
      const s = run.current.edges[id]!;
      expect(x.status).toBe(s.worst);
      expect(x.saturation).toBe(Math.max(...s.saturation));
      expect(x.level).toBe(congestionPosition(s.worst, x.saturation));
    }
  });
});
