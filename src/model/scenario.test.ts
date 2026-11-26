import { describe, expect, it } from 'vitest';
import { bundle } from '../data';
import {
  basisFacts,
  buildInput,
  computeScenario,
  crowdOf,
  crowdRange,
  defaultParams,
  deriveKpis,
  directionWalkMinutes,
  endTimeOf,
  endWindowText,
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
  it('지하철 이용 비율 예측이 없으면 차량을 뺀 전원이 역으로 간다고 본다', () => {
    expect(defaultParams(prediction).stationShare).toBeCloseTo(0.82, 9);
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

describe('지하철 이용 인원 예측이 있는 공연', () => {
  // 관객 7,860명 중 42%가 지하철을 타러 간다. 그 인원의 80% 범위는 2,641~5,007명
  const partial = { ...prediction, rail: { ratio: 0.42, people: 3301, peopleLow: 2641, peopleHigh: 5007, interval: 'p10-p90' } };

  it('지하철 이용 비율을 예측값으로 시작한다', () => {
    expect(defaultParams(partial).stationShare).toBe(0.42);
  });
  it('엔진에는 관객 전체와 지하철 이용 비율을 넘긴다', () => {
    const input = buildInput(bundle, partial, { ...defaultParams(partial), attendance: 10000, vehicleRatio: 0.2 });
    expect(input.attendance).toBe(10000);
    expect(input.stationShare).toBeCloseTo(0.42, 9);
    expect(input.vehicle!.ratio).toBe(0.2);
  });
  it('차량 비율과 합쳐 100%를 넘으면 역으로 가는 사람을 줄인다', () => {
    const crowd = crowdOf({ ...defaultParams(partial), attendance: 10000, vehicleRatio: 0.7 });
    expect(crowd.station).toBeCloseTo(3000, 6);
  });
  it('인원 범위는 지하철 이용 인원의 예측 범위를 따른다', () => {
    const { low, high } = crowdRange(partial, defaultParams(partial));
    // 관객 수는 6,900~8,700명, 역으로 가는 사람은 3,301명 × (2,641 ÷ 3,301) ~ × (5,007 ÷ 3,301)
    expect(low).toEqual({ attendance: 6900, station: 2641 });
    expect(high).toEqual({ attendance: 8700, station: 5007 });
  });
  it('운영자가 관객 수를 바꾸면 범위도 같은 비율로 옮긴다', () => {
    const { low, high } = crowdRange(partial, { ...defaultParams(partial), attendance: 15720 });
    // 관객 수가 두 배면 지하철 이용 인원 범위도 두 배다
    expect(low.station).toBe(2 * 2641);
    expect(high.station).toBe(2 * 5007);
  });
  it('지하철 이용 인원 범위가 없으면 관객 수 범위를 따른다', () => {
    const { low, high } = crowdRange(prediction, defaultParams(prediction));
    expect(low.station).toBe(Math.round(7860 * 0.82 * (6900 / 7860)));
    expect(high.station).toBe(Math.round(7860 * 0.82 * (8700 / 7860)));
  });
  it('역으로 가는 사람이 줄면 최대 대기도 줄어든다', () => {
    const all = computeScenario(bundle, prediction, defaultParams(prediction));
    const some = computeScenario(bundle, partial, defaultParams(partial));
    expect(some.crowd.station).toBeCloseTo(7860 * 0.42, 6);
    expect(some.current.totals.modePeople.station).toBeCloseTo(7860 * 0.42, 6);
    expect(some.current.totals.modePeople.other).toBeCloseTo(7860 * (1 - 0.42 - 0.18), 6);
    expect(some.current.totals.peakQueueTotal).toBeLessThan(all.current.totals.peakQueueTotal);
    expect(some.range.stationLow).toBe(2641);
    expect(some.range.stationHigh).toBe(5007);
  });
});

describe('종료 시각 예측', () => {
  it('범위가 없으면 공연 목록의 종료 시각 하나로 본다', () => {
    expect(endTimeOf(event, prediction)).toEqual({ expectedAt: event.endAt, window: null, sdMinutes: null, interval: null, entered: false });
  });
  it('범위가 있으면 예상 시각 대비 분으로 옮긴다', () => {
    const t = endTimeOf(event, {
      ...prediction,
      endTime: {
        expectedAt: '2026-11-21T21:40:00+09:00',
        lowAt: '2026-11-21T21:17:00+09:00',
        highAt: '2026-11-21T22:03:00+09:00',
        sdMinutes: 17.9,
        interval: 'p10-p90',
      },
    });
    expect(t.window).toEqual({ earlyMinutes: -23, lateMinutes: 23 });
    expect(t.sdMinutes).toBe(17.9);
  });
  it('운영자가 실제 종료 시각을 넣으면 그 시각을 쓰고 범위를 두지 않는다', () => {
    const ranged = {
      ...prediction,
      endTime: { expectedAt: event.endAt, lowAt: '2026-11-21T21:17:00+09:00', highAt: '2026-11-21T22:03:00+09:00', sdMinutes: 17.9, interval: 'p10-p90' },
    };
    expect(endTimeOf(event, ranged, '2026-11-21T21:52:00+09:00')).toEqual({
      expectedAt: '2026-11-21T21:52:00+09:00',
      window: null,
      sdMinutes: null,
      interval: null,
      entered: true,
    });
    expect(endTimeOf(event, ranged, null).entered).toBe(false);
  });
  it('범위는 앞뒤가 같으면 ±로, 다르면 따로 적는다', () => {
    expect(endWindowText({ earlyMinutes: -23, lateMinutes: 23 })).toBe('±23분');
    expect(endWindowText({ earlyMinutes: -20, lateMinutes: 25 })).toBe('−20분~+25분');
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

  it('인원 장부: 역으로 걷는 사람 + 차량 탑승자 + 그 밖 = 관객 수', () => {
    const m = s.current.totals.modePeople;
    expect(m.station + m.vehicle + m.other).toBeCloseTo(7860, 6);
    expect(m.vehicle).toBeCloseTo(7860 * 0.18, 6);
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
    // 대기와 추가 보행은 역으로 걷는 사람(차량 18%를 뺀 인원)이 진다
    expect(k.perPerson.value).toBeCloseTo((best.queueMinutesTotal + best.detourMinutesTotal) / (7860 * 0.82), 9);
    expect(k.maxQueueMinute).toBe(best.peakQueueTotalMinute);
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

  it('보행 안내 없이 차량 출구 배정만 바뀌면 그 효과를 적는다', () => {
    // 역으로 가는 사람이 적으면 보행 분산은 이득이 없고 차량 출구 배정만 바뀐다
    const few = computeScenario(bundle, prediction, { ...defaultParams(prediction), stationShare: 0.3 });
    expect(few.best.plan.guidance.moves).toEqual([]);
    const saved = few.current.totals.vehicleQueuePersonMinutes - few.best.result.totals.vehicleQueuePersonMinutes;
    expect(saved).toBeGreaterThan(1);
    const text = summarySentence(few);
    expect(text).toContain('보행 안내는');
    expect(text).toContain(`차량 대기가 ${formatNumber(saved)}사람·분 줄어듭니다`);
  });

  it('안내도 차량 배정도 바꿀 것이 없으면 현재 운영안을 유지한다고 적는다', () => {
    const none = computeScenario(bundle, prediction, { ...defaultParams(prediction), stationShare: 0.3, vehicleRatio: 0 });
    expect(none.best.plan.guidance.moves).toEqual([]);
    expect(summarySentence(none)).toContain('현재 운영안을 유지합니다');
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
