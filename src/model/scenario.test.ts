import { describe, expect, it } from 'vitest';
import { bundle } from '../data';
import {
  basisFacts,
  buildInput,
  computeScenario,
  defaultParams,
  deriveKpis,
  directionWalkMinutes,
  summarySentence,
  venueHasNetwork,
} from './scenario';
import { formatNumber } from '../lib/format';

const event = bundle.events.events.find((e) => e.id === 'kspo-20261121-a')!;
const prediction = bundle.predictions.predictions.find((p) => p.eventId === event.id)!;

describe('defaultParams', () => {
  it('AI 예측값으로 시작한다', () => {
    const p = defaultParams(prediction);
    expect(p.attendance).toBe(7860);
    expect(p.egressTotalMinutes).toBe(22);
    expect(p.vehicleRatio).toBe(0.18);
    expect(p.allowedDirections).toEqual(['A', 'B', 'C']);
  });
});

describe('buildInput', () => {
  it('퇴장 분포 시간을 바꾸면 정점 시각도 같은 비율로 바뀐다', () => {
    const input = buildInput(bundle, prediction, { ...defaultParams(prediction), egressTotalMinutes: 33 });
    expect(input.egress).toEqual({ kind: 'gamma', totalMinutes: 33, peakMinute: 9 });
  });
  it('운영자 입력(반응률, 차량 비율)을 그대로 넘긴다', () => {
    const input = buildInput(bundle, prediction, { ...defaultParams(prediction), compliance: 0.4, vehicleRatio: 0.2 });
    expect(input.compliance).toBe(0.4);
    expect(input.vehicle!.ratio).toBe(0.2);
  });
});

describe('computeScenario', () => {
  const s = computeScenario(bundle, prediction, defaultParams(prediction));

  it('분산 운영안이 현재 운영안보다 부담이 작다', () => {
    expect(s.best.result.totals.burdenPersonMinutes).toBeLessThan(s.current.totals.burdenPersonMinutes);
  });

  it('퇴장 인원 범위 하단·상단으로 다시 계산한 최대 대기가 분산안 값을 감싼다', () => {
    const q = s.best.result.totals.peakQueueTotal;
    expect(s.range.low.totals.peakQueueTotal).toBeLessThan(q);
    expect(s.range.high.totals.peakQueueTotal).toBeGreaterThan(q);
    expect(s.range.attendanceLow).toBe(6900);
    expect(s.range.attendanceHigh).toBe(8700);
  });
});

describe('deriveKpis', () => {
  it('현재 대비 감소율과 1인당 시간을 계산 결과에서 만든다', () => {
    const s = computeScenario(bundle, prediction, defaultParams(prediction));
    const k = deriveKpis(s);
    const cur = s.current.totals;
    const best = s.best.result.totals;
    expect(k.maxQueue.value).toBe(best.peakQueueTotal);
    expect(k.maxQueue.reduction).toBeCloseTo(1 - best.peakQueueTotal / cur.peakQueueTotal, 9);
    expect(k.walkBurden.value).toBeCloseTo(best.queueMinutesTotal + best.detourMinutesTotal, 9);
    expect(k.perPerson.value).toBeCloseTo((best.queueMinutesTotal + best.detourMinutesTotal) / 7860, 9);
    expect(k.clear.value).toBe(best.clearMinute);
  });
});

describe('설정 카드 문장', () => {
  const s = computeScenario(bundle, prediction, defaultParams(prediction));

  it('요약 문장의 숫자는 계산 결과의 차이에서 나온다', () => {
    const c = s.current.totals;
    const b = s.best.result.totals;
    const text = summarySentence(s);
    expect(text).toContain(formatNumber(c.peakQueueTotal - b.peakQueueTotal) + '명');
    expect(text).toContain(formatNumber(c.queueMinutesTotal + c.detourMinutesTotal - (b.queueMinutesTotal + b.detourMinutesTotal)) + '사람·분');
    expect(text).toContain('60%');
  });

  it('계산 근거는 가장 붐비는 병목의 그 시각 유입·대기·용량을 보여준다', () => {
    const b = basisFacts(bundle, s)!;
    expect(b).not.toBeNull();
    const e = s.current.edges[s.current.totals.maxQueueEdgeId!]!;
    const t = s.current.totals.maxQueueMinute - 1;
    expect(b.minute).toBe(s.current.totals.maxQueueMinute);
    expect(b.inflow).toBeCloseTo(e.arrivals[t]!, 9);
    expect(b.queue).toBeCloseTo(e.queue[t]!, 9);
    expect(b.capacity).toBeCloseTo(e.capacityPerMin, 9);
    const from = bundle.network.edges.find((x) => x.id === s.current.totals.maxQueueEdgeId)!.from;
    expect(b.placeName).toBe(bundle.network.nodes.find((n) => n.id === from)!.name);
  });
});

describe('보조 함수', () => {
  it('방면별 기본 보행 시간은 출구 비율로 가중 평균한다 (A < B < C)', () => {
    const w = directionWalkMinutes(bundle);
    expect(w.A).toBeLessThan(w.B!);
    expect(w.B).toBeLessThan(w.C!);
  });
  it('길 데이터가 있는 공연장만 계산할 수 있다', () => {
    expect(venueHasNetwork(bundle, 'kspo-dome')).toBe(true);
    expect(venueHasNetwork(bundle, 'olympic-hall')).toBe(false);
  });
});
