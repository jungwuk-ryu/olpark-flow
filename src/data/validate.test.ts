import { describe, expect, it } from 'vitest';
import { validateData } from './validate';
import type { DataBundle } from './contract';
import { testAssumptions } from '../engine/testing/fixtures';

/** 최소한의 올바른 데이터 묶음 */
function valid(): DataBundle {
  return {
    events: {
      schemaVersion: 1,
      events: [
        {
          id: 'ev1',
          name: '공연 A',
          venueId: 'kspo-dome',
          venueName: 'KSPO DOME',
          startAt: '2026-11-21T19:00:00+09:00',
          endAt: '2026-11-21T21:40:00+09:00',
          seatCapacity: 9500,
        },
      ],
    },
    predictions: {
      schemaVersion: 1,
      generatedAt: '2026-11-21T12:00:00+09:00',
      model: { name: 'm', version: '0' },
      predictions: [
        {
          eventId: 'ev1',
          genre: { value: '아이돌', confidence: '높음', sources: [{ title: 't', url: 'https://example.com', quote: 'q' }] },
          attendance: { value: 7860, low: 6900, high: 8700, interval: 'p10-p90' },
          egressCurve: { totalMinutes: 22, totalMinutesLow: 20, totalMinutesHigh: 24, peakMinute: 6 },
          directionShare: { A: 0.72, B: 0.19, C: 0.09 },
          vehicle: { ratio: 0.18, low: 0.14, high: 0.22 },
          similarEvents: Array.from({ length: 5 }, (_, i) => ({
            name: `공연 ${i}`,
            date: '2026-01-01',
            genre: '아이돌' as const,
            attendance: 8000,
            vehicleRatio: 0.2,
          })),
        },
      ],
    },
    notices: { schemaVersion: 1, notices: [] },
    network: {
      schemaVersion: 1,
      nodes: [
        { id: 'X', kind: 'exit', name: 'X', lngLat: [127.127, 37.52], venueId: 'kspo-dome', exitShare: 1, source: '가정' },
        { id: 'C', kind: 'crossing', name: 'C', lngLat: [127.128, 37.519], source: '가정' },
        { id: 'DA', kind: 'destination', name: 'DA', lngLat: [127.13, 37.516], direction: 'A', source: '가정' },
      ],
      edges: [
        { id: 'e1', from: 'X', to: 'C', lengthM: 100, widthM: 4, type: 'flat', queueAreaM2: 100, stepFree: true, source: '가정' },
        { id: 'e2', from: 'C', to: 'DA', lengthM: 100, widthM: 4, type: 'flat', queueAreaM2: 100, stepFree: true, source: '가정' },
      ],
    },
    routes: {
      schemaVersion: 1,
      routes: [{ direction: 'A', name: 'A', destinationNodeId: 'DA', paths: [{ exitNodeId: 'X', edgeIds: ['e1', 'e2'] }] }],
    },
    parking: {
      schemaVersion: 1,
      lots: [{ id: 'P1', name: 'P1', capacity: 562, exitIds: ['G'], source: '관측' }],
      exits: [{ id: 'G', name: 'G', throughputPerMin: 10, crossingNodeIds: ['C'], source: '가정' }],
    },
    assumptions: testAssumptions,
  };
}

const has = (errors: string[], text: string) => errors.some((e) => e.includes(text));

describe('validateData', () => {
  it('올바른 데이터는 오류가 없다', () => {
    expect(validateData(valid())).toEqual([]);
  });

  it('없는 장소를 잇는 길 토막을 찾는다', () => {
    const d = valid();
    d.network.edges[1]!.to = 'NOPE';
    expect(has(validateData(d), 'NOPE')).toBe(true);
  });

  it('공연장별 출구 비율 합이 1이 아니면 오류', () => {
    const d = valid();
    d.network.nodes[0]!.exitShare = 0.8;
    expect(has(validateData(d), 'exitShare')).toBe(true);
  });

  it('올림픽공원에서 먼 좌표를 찾는다 (위도·경도 뒤바뀜 포함)', () => {
    const d = valid();
    d.network.nodes[0]!.lngLat = [37.52, 127.127];
    expect(has(validateData(d), '좌표')).toBe(true);
  });

  it('경로가 끊겨 있으면 오류', () => {
    const d = valid();
    d.routes.routes[0]!.paths[0]!.edgeIds = ['e2', 'e1'];
    expect(has(validateData(d), '이어지지')).toBe(true);
  });

  it('도착점의 방면이 경로 방면과 다르면 오류', () => {
    const d = valid();
    d.network.nodes[2]!.direction = 'B';
    expect(has(validateData(d), '방면')).toBe(true);
  });

  it('주차장이 없는 출구나 교차점을 가리키면 오류', () => {
    const d = valid();
    d.parking.lots[0]!.exitIds = ['NOGATE'];
    d.parking.exits[0]!.crossingNodeIds = ['NOCROSS'];
    const e = validateData(d);
    expect(has(e, 'NOGATE')).toBe(true);
    expect(has(e, 'NOCROSS')).toBe(true);
  });

  it('예측 범위·방면 비율·비슷한 공연 수를 검사한다', () => {
    const d = valid();
    const p = d.predictions.predictions[0]!;
    p.attendance = { value: 9000, low: 6900, high: 8700, interval: 'p10-p90' };
    p.directionShare = { A: 0.7, B: 0.2, C: 0.2 };
    p.similarEvents = p.similarEvents.slice(0, 3);
    p.egressCurve = { totalMinutes: 10, totalMinutesLow: 9, totalMinutesHigh: 12, peakMinute: 12 };
    const e = validateData(d);
    expect(has(e, 'attendance')).toBe(true);
    expect(has(e, 'directionShare')).toBe(true);
    expect(has(e, 'similarEvents')).toBe(true);
    expect(has(e, 'peakMinute')).toBe(true);
  });

  it('예측하지 못한 값(null)은 오류가 아니다', () => {
    const d = valid();
    const p = d.predictions.predictions[0]!;
    p.vehicle = null;
    p.genre = null;
    expect(validateData(d)).toEqual([]);
  });

  it('없는 공연의 예측, 없는 길의 통제를 찾는다', () => {
    const d = valid();
    d.predictions.predictions[0]!.eventId = 'ghost';
    d.notices.notices.push({
      id: 'n1',
      segmentName: 's',
      edgeIds: ['ghost-edge'],
      startAt: '2026-11-18T00:00:00+09:00',
      endAt: '2026-11-24T23:59:00+09:00',
      effect: 'closed',
      widthM: null,
      content: 'c',
      evidence: { quote: 'q', url: 'https://example.com', publishedAt: '2026-11-15' },
      confidence: '높음',
    });
    const e = validateData(d);
    expect(has(e, 'ghost')).toBe(true);
    expect(has(e, 'ghost-edge')).toBe(true);
  });
});
