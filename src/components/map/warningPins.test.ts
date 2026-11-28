import { describe, expect, it } from 'vitest';
import walkways from '../../data/walkways.json';
import sample from '../../api/testing/server-bundle.sample.json';
import { fromServer, type ServerBundle } from '../../api';
import { NO_CONTROLS, type LngLat, type Status, type WalkGraph, type WalkResult } from '../../engine';
import { defaultParams } from '../../model/scenario';
import { edgeGeometry, narrowEdge, runWalk } from '../../model/walkplan';
import { PIN_MAX, PIN_MERGE_M, PINS_BELOW_ZOOM, showPins, warningPins } from './warningPins';

/** 위도 37.5에서 동쪽으로 m미터 떨어진 점 */
const east = (from: LngLat, m: number): LngLat => [from[0] + m / (111_320 * Math.cos((from[1] * Math.PI) / 180)), from[1]];
const origin: LngLat = [127.12, 37.52];

/** 길마다 분별 상태와 길 위 인원만 있는 계산 결과 */
function resultOf(edges: Record<string, { status: Status[]; occupancy: number[] }>): WalkResult {
  const out: Record<string, unknown> = {};
  for (const [id, e] of Object.entries(edges)) {
    const rank = { 여유: 0, 보통: 1, 병목: 2, 위험: 3 } as const;
    const worst = e.status.reduce<Status>((w, s) => (rank[s] > rank[w] ? s : w), '여유');
    out[id] = { edgeId: id, status: e.status, occupancy: e.occupancy, worst };
  }
  return { minutes: 3, edges: out } as unknown as WalkResult;
}

describe('경고 핀', () => {
  const far = (i: number) => east(origin, i * 200);
  const mid = new Map<string, LngLat>([
    ['a', far(0)],
    ['b', far(1)],
    ['c', far(2)],
  ]);
  const result = resultOf({
    a: { status: ['여유', '위험', '여유'], occupancy: [0, 100, 0] },
    b: { status: ['위험', '병목', '여유'], occupancy: [80, 60, 0] },
    c: { status: ['여유', '위험', '위험'], occupancy: [0, 300, 50] },
  });

  it('시간별 보기는 그 분에 위험한 길에만 핀을 띄운다 (병목은 띄우지 않는다)', () => {
    expect(warningPins(result, 1, mid, []).map((p) => p.edgeIds)).toEqual([['b']]);
    expect(warningPins(result, 3, mid, []).map((p) => p.edgeIds)).toEqual([['c']]);
  });

  it('길 위에 선 사람이 많은 곳부터 놓는다', () => {
    expect(warningPins(result, 2, mid, []).map((p) => p.edgeIds[0])).toEqual(['c', 'a']);
  });

  it('전체 시간대 보기는 저녁 중 한 번이라도 위험했던 길이고, 가장 많이 서 있던 인원으로 줄 세운다', () => {
    const pins = warningPins(result, null, mid, []);
    expect(pins.map((p) => p.edgeIds[0])).toEqual(['c', 'a', 'b']);
    expect(pins[0]!.standing).toBe(300);
  });

  it('가까운 위험 구간은 가장 붐빈 길 자리의 핀 하나로 묶는다', () => {
    const near = new Map<string, LngLat>([
      ['a', origin],
      ['b', east(origin, PIN_MERGE_M - 5)],
      ['c', east(origin, PIN_MERGE_M * 3)],
    ]);
    const crowded = resultOf({
      a: { status: ['위험', '여유', '여유'], occupancy: [50, 0, 0] },
      b: { status: ['위험', '여유', '여유'], occupancy: [90, 0, 0] },
      c: { status: ['위험', '여유', '여유'], occupancy: [10, 0, 0] },
    });
    const pins = warningPins(crowded, 1, near, []);
    expect(pins).toHaveLength(2);
    expect(pins[0]).toMatchObject({ edgeIds: ['b', 'a'], at: near.get('b'), count: 2, standing: 140 });
    expect(pins[1]).toMatchObject({ edgeIds: ['c'], count: 1 });
  });

  it(`핀은 많아야 ${PIN_MAX}개다`, () => {
    const many = new Map<string, LngLat>();
    const edges: Record<string, { status: Status[]; occupancy: number[] }> = {};
    for (let i = 0; i < PIN_MAX + 3; i++) {
      many.set(`e${i}`, far(i));
      edges[`e${i}`] = { status: ['위험', '여유', '여유'], occupancy: [10 + i, 0, 0] };
    }
    const pins = warningPins(resultOf(edges), 1, many, []);
    expect(pins).toHaveLength(PIN_MAX);
    expect(pins[0]!.edgeIds).toEqual([`e${PIN_MAX + 2}`]);
  });

  it('통제·장애물·안전요원 표시와 겹치는 핀은 위로 띄운다', () => {
    const pins = warningPins(result, 2, mid, [east(far(2), 5)]);
    expect(pins.find((p) => p.edgeIds[0] === 'c')!.lift).toBe(true);
    expect(pins.find((p) => p.edgeIds[0] === 'a')!.lift).toBe(false);
  });

  it('위험한 길이 없거나 계산 범위 밖의 시점이면 핀이 없다', () => {
    expect(warningPins(result, 0, mid, [])).toEqual([]);
    expect(warningPins(resultOf({ a: { status: ['여유', '보통', '병목'], occupancy: [1, 2, 3] } }), null, mid, [])).toEqual([]);
  });

  it('공원 전체를 볼 만큼 축소했을 때만 보인다', () => {
    expect(showPins(PINS_BELOW_ZOOM - 0.1)).toBe(true);
    expect(showPins(PINS_BELOW_ZOOM)).toBe(false);
    expect(showPins(17)).toBe(false);
  });
});

describe('실제 보행로에서 길을 좁힌 저녁', () => {
  // 올림픽홀 공연에서 가장 많이 지나는 길을 0.5m로 좁히면 출구 앞 보도에 사람이 꽉 찬다
  const graph = walkways as unknown as WalkGraph;
  const data = fromServer(sample as unknown as ServerBundle);
  const event = data.events.events.find((e) => e.id === 'olympichall-20261127-2000')!;
  const prediction = data.predictions.predictions.find((p) => p.eventId === event.id)!;
  const base = runWalk(data, graph, event, prediction, defaultParams(prediction), NO_CONTROLS)!;
  const busiest = Object.values(base.baseline.edges).sort((a, b) => b.people - a.people)[0]!;
  const run = runWalk(data, graph, event, prediction, defaultParams(prediction), narrowEdge(NO_CONTROLS, busiest.edgeId, 0.5))!;
  const mid = edgeGeometry(graph).mid;

  it('위험한 분에는 핀이 뜨고, 핀의 길은 모두 그 분에 위험하다', () => {
    let shown = 0;
    for (let minute = 1; minute <= run.current.minutes; minute++) {
      const pins = warningPins(run.current, minute, mid, [mid.get(busiest.edgeId)!]);
      expect(pins.length).toBeLessThanOrEqual(PIN_MAX);
      for (const pin of pins) for (const id of pin.edgeIds) expect(run.current.edges[id]!.status[minute - 1]).toBe('위험');
      if (pins.length) shown++;
    }
    expect(shown).toBeGreaterThan(10);
  });
});
