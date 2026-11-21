import { optimize, simulate, type Direction, type SimulationInput, type SimulationResult, DIRECTIONS } from '../engine';
import type { Plan } from '../engine/optimize';
import type { DataBundle, Prediction, VenueId } from '../data/contract';
import { formatNumber } from '../lib/format';

/** 운영자가 화면에서 바꾸는 조건 */
export interface ScenarioParams {
  attendance: number;
  egressTotalMinutes: number;
  compliance: number;
  staff: number;
  allowedDirections: Direction[];
  vehicleRatio: number;
}

export const PARAM_LIMITS = {
  attendance: { min: 2000, max: 12000, step: 10 },
  egressTotalMinutes: { min: 16, max: 40, step: 1 },
  compliance: { min: 0, max: 1, step: 0.05 },
  staff: { min: 2, max: 12, step: 1 },
} as const;

/**
 * 분산안 탐색에서 1인당 더 걷게 하는 시간 상한 (분, 가정).
 * 실제 지도에서 한성백제역은 올림픽공원역보다 약 13분 더 멀어 10분으로는 분산안이 거의 나오지 않는다.
 */
export const MAX_DETOUR_MINUTES = 20;
/** 안내 반응률 기본값 (가정) */
export const DEFAULT_COMPLIANCE = 0.6;
/** 안내요원 기본값 */
export const DEFAULT_STAFF = 6;

export function defaultParams(p: Prediction): ScenarioParams {
  return {
    attendance: p.attendance?.value ?? 5000,
    egressTotalMinutes: p.egressCurve?.totalMinutes ?? 22,
    compliance: DEFAULT_COMPLIANCE,
    staff: DEFAULT_STAFF,
    allowedDirections: [...DIRECTIONS],
    vehicleRatio: p.vehicle?.ratio ?? 0,
  };
}

/** 정점 시각은 예측의 "정점 ÷ 총 시간" 비율을 유지한다 */
function peakFor(p: Prediction, total: number): number {
  const ratio = p.egressCurve ? p.egressCurve.peakMinute / p.egressCurve.totalMinutes : 0.27;
  return Math.min(total - 1, Math.max(1, Math.round(total * ratio)));
}

export function buildInput(data: DataBundle, p: Prediction, params: ScenarioParams, attendance = params.attendance): SimulationInput {
  return {
    network: data.network,
    routes: data.routes,
    assumptions: data.assumptions,
    attendance,
    egress: { kind: 'gamma', totalMinutes: params.egressTotalMinutes, peakMinute: peakFor(p, params.egressTotalMinutes) },
    directionShare: p.directionShare ?? { A: 1 },
    compliance: params.compliance,
    horizonMinutes: 90,
    vehicle: params.vehicleRatio > 0 ? { parking: data.parking, ratio: params.vehicleRatio } : undefined,
  };
}

export interface ScenarioResult {
  params: ScenarioParams;
  current: SimulationResult;
  best: { plan: Plan; result: SimulationResult };
  /** 분산안을 퇴장 인원 범위 하단·상단으로 다시 계산한 결과 */
  range: { attendanceLow: number; attendanceHigh: number; low: SimulationResult; high: SimulationResult };
  evaluated: number;
}

export function computeScenario(data: DataBundle, p: Prediction, params: ScenarioParams): ScenarioResult {
  const input = buildInput(data, p, params);
  const opt = optimize(input, {
    staff: params.staff,
    maxDetourPerPersonMinutes: MAX_DETOUR_MINUTES,
    allowedDirections: params.allowedDirections,
  });
  const lowRatio = p.attendance ? p.attendance.low / p.attendance.value : 1;
  const highRatio = p.attendance ? p.attendance.high / p.attendance.value : 1;
  const attendanceLow = Math.round(params.attendance * lowRatio);
  const attendanceHigh = Math.round(params.attendance * highRatio);
  const withPlan = (n: number) =>
    simulate({
      ...buildInput(data, p, params, n),
      guidance: opt.best.plan.guidance.moves.length ? opt.best.plan.guidance : undefined,
      vehicle: input.vehicle && opt.best.plan.vehicleExitShare ? { ...input.vehicle, exitShare: opt.best.plan.vehicleExitShare } : input.vehicle,
    });
  return {
    params,
    current: opt.current,
    best: opt.best,
    range: { attendanceLow, attendanceHigh, low: withPlan(attendanceLow), high: withPlan(attendanceHigh) },
    evaluated: opt.evaluated,
  };
}

export interface Kpi {
  value: number | null;
  current: number | null;
  /** 현재 대비 감소 비율 (0.67 = 67% 감소). 늘었으면 음수 */
  reduction: number | null;
}

const kpi = (value: number | null, current: number | null): Kpi => ({
  value,
  current,
  reduction: value !== null && current !== null && current !== 0 ? 1 - value / current : null,
});

export function deriveKpis(s: ScenarioResult) {
  const b = s.best.result.totals;
  const c = s.current.totals;
  const walkB = b.queueMinutesTotal + b.detourMinutesTotal;
  const walkC = c.queueMinutesTotal + c.detourMinutesTotal;
  const people = s.params.attendance;
  return {
    maxQueue: kpi(b.peakQueueTotal, c.peakQueueTotal),
    maxQueueRange: { low: s.range.low.totals.peakQueueTotal, high: s.range.high.totals.peakQueueTotal },
    walkBurden: kpi(walkB, walkC),
    perPerson: kpi(people ? walkB / people : 0, people ? walkC / people : 0),
    clear: kpi(b.clearMinute, c.clearMinute),
  };
}

/** 설정 카드의 결과 요약 문장. 숫자는 모두 계산 결과의 차이에서 만든다 */
export function summarySentence(s: ScenarioResult): string {
  const c = s.current.totals;
  const b = s.best.result.totals;
  const pct = formatNumber(s.params.compliance * 100);
  const moves = s.best.plan.guidance.moves;
  if (!moves.length) return `지금 조건에서는 안내를 바꿔도 부담이 줄지 않아 현재 운영안을 유지합니다. 안내 반응률 ${pct}%를 반영했습니다.`;
  const dq = c.peakQueueTotal - b.peakQueueTotal;
  const dw = c.queueMinutesTotal + c.detourMinutesTotal - (b.queueMinutesTotal + b.detourMinutesTotal);
  const part = (v: number, unit: string) => `${formatNumber(Math.abs(v))}${unit}${v >= 0 ? '' : ' 늘어'}`;
  const verb = dq >= 0 && dw >= 0 ? '줄어듭니다' : '바뀝니다';
  return `${moves[0]!.from} 방면 집중을 완화해 최대 대기 ${part(dq, '명')}, 대기+추가 보행 ${part(dw, '사람·분')}이 ${verb}. 안내 반응률 ${pct}%를 반영했습니다.`;
}

export interface BasisFacts {
  placeName: string;
  minute: number;
  inflow: number;
  queue: number;
  capacity: number;
  widthM: number;
  perMeter: number;
  edgeType: string;
}

/** 현재 운영안에서 가장 붐비는 병목의 그 시각 수치 (계산 근거 한 줄) */
export function basisFacts(data: DataBundle, s: ScenarioResult): BasisFacts | null {
  const id = s.current.totals.maxQueueEdgeId;
  if (!id) return null;
  const e = data.network.edges.find((x) => x.id === id)!;
  const series = s.current.edges[id]!;
  const t = Math.max(0, s.current.totals.maxQueueMinute - 1);
  const f = data.assumptions.flowPerMeterPerMin;
  const perMeter = e.type === 'stairs' ? f.flat * f.stairsFactor : e.type === 'gate' ? f.gate : e.type === 'ramp' ? f.ramp : f.flat;
  return {
    placeName: data.network.nodes.find((n) => n.id === e.from)?.name ?? e.id,
    minute: s.current.totals.maxQueueMinute,
    inflow: series.arrivals[t]!,
    queue: series.queue[t]!,
    capacity: series.capacityPerMin,
    widthM: e.widthM,
    perMeter,
    edgeType: { flat: '통로', ramp: '경사로', gate: '문', stairs: '계단' }[e.type],
  };
}

/** 방면별 기본 보행 시간 (분). 출구 비율로 가중 평균 */
export function directionWalkMinutes(data: DataBundle): Partial<Record<Direction, number>> {
  const edge = new Map(data.network.edges.map((e) => [e.id, e]));
  const share = new Map(data.network.nodes.map((n) => [n.id, n.exitShare ?? 0]));
  const out: Partial<Record<Direction, number>> = {};
  for (const r of data.routes.routes) {
    let w = 0;
    let s = 0;
    for (const p of r.paths) {
      const m = p.edgeIds.reduce((acc, id) => acc + (edge.get(id)?.lengthM ?? 0), 0) / data.assumptions.walkSpeedMPerMin;
      const x = share.get(p.exitNodeId) ?? 0;
      w += m * x;
      s += x;
    }
    out[r.direction] = s > 0 ? w / s : 0;
  }
  return out;
}

export function venueHasNetwork(data: DataBundle, venueId: VenueId): boolean {
  return data.network.nodes.some((n) => n.kind === 'exit' && n.venueId === venueId);
}
