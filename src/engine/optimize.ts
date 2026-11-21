import { simulate } from './simulate';
import { defaultExitShare, normalize } from './vehicle';
import type { Direction, GuidanceMove, GuidancePlan, SimulationInput, SimulationResult } from './types';

export interface OptimizeConstraints {
  /** 안내요원 수. 동시에 운영할 수 있는 분기점 수 = 안내요원 ÷ 분기점당 인원(assumptions.staffPerJunction) */
  staff: number;
  /** 다른 방면으로 안내할 때 1인당 더 걷게 되는 시간 상한 (분) */
  maxDetourPerPersonMinutes?: number;
  /** 안내 비율 격자 간격 (기본 0.05) */
  ratioStep?: number;
  /** 한 분기점에서 다른 방면으로 돌리는 비율 합의 상한 (기본 0.6) */
  maxRatio?: number;
  /** 안내 시작 시각 후보 (기본 [0]) */
  startMinutes?: number[];
  /** 차량 출구 배정 격자 간격 (기본 0.05) */
  vehicleShareStep?: number;
  /** 차량 출구마다 최소 배정 비율 (기본 0.15). 출구를 사실상 닫는 안을 막는다 */
  minVehicleExitShare?: number;
  /** 안내 대상으로 운영할 방면 (기본: 전부). 여기 없는 방면으로는 돌리지 않는다 */
  allowedDirections?: Direction[];
}

export const DEFAULT_MIN_VEHICLE_EXIT_SHARE = 0.15;

export interface Plan {
  guidance: GuidancePlan;
  vehicleExitShare?: Record<string, number>;
}

export interface OptimizeResult {
  /** 현재 운영안 (안내 없음, 입력한 차량 배정) */
  current: SimulationResult;
  best: { plan: Plan; result: SimulationResult };
  /** 계산해 본 후보 수 */
  evaluated: number;
}

/** 합계가 이 비율 안으로 비슷하면 안내 변경이 적은 안을 고른다 */
const TIE_TOLERANCE = 0.005;

/**
 * 분산안 탐색: 분기점 안내 비율 × 안내 시작 시각 × 차량 출구 배정을 격자로 계산해
 * "대기 + 추가 보행 (+ 차량 대기)" 합계가 가장 작은 안을 고른다.
 *
 * - 안내요원 수는 동시에 운영할 분기점 수만 제한한다. 길 용량은 늘리지 않는다.
 * - 안내 효과는 안내 비율 × 안내 반응률(input.compliance)로만 반영된다.
 * - 분기점이 여러 곳이면 한 곳씩 차례로 정하는 탐욕적 탐색이다 (전체 조합 탐색 아님).
 */
export function optimize(input: SimulationInput, c: OptimizeConstraints): OptimizeResult {
  const step = c.ratioStep ?? 0.05;
  const maxRatio = c.maxRatio ?? 0.6;
  const startMinutes = c.startMinutes ?? [0];
  const maxDetour = c.maxDetourPerPersonMinutes ?? Infinity;
  const perJunction = Math.max(1, input.assumptions.staffPerJunction);
  const allowedJunctions = Math.floor(Math.max(0, c.staff) / perJunction);

  const baseInput: SimulationInput = { ...input, guidance: undefined };
  const current = simulate(baseInput);
  let evaluated = 1;

  const currentVehicleShare = input.vehicle
    ? input.vehicle.exitShare
      ? normalize(input.vehicle.exitShare, input.vehicle.parking.exits)
      : defaultExitShare(input.vehicle.parking)
    : undefined;

  const run = (plan: Plan) => {
    evaluated++;
    return simulate({
      ...input,
      guidance: plan.guidance.moves.length ? plan.guidance : undefined,
      vehicle: input.vehicle && plan.vehicleExitShare ? { ...input.vehicle, exitShare: plan.vehicleExitShare } : input.vehicle,
    });
  };
  const changeAmount = (p: Plan) =>
    p.guidance.moves.reduce((s, m) => s + m.ratio, 0) +
    (p.vehicleExitShare && currentVehicleShare
      ? Object.keys(currentVehicleShare).reduce((s, k) => s + Math.abs((p.vehicleExitShare![k] ?? 0) - currentVehicleShare[k]!), 0)
      : 0);
  const better = (a: { plan: Plan; result: SimulationResult }, b: { plan: Plan; result: SimulationResult }) => {
    const x = a.result.totals.burdenPersonMinutes;
    const y = b.result.totals.burdenPersonMinutes;
    const tol = Math.max(1e-6, Math.abs(y) * TIE_TOLERANCE);
    if (x < y - tol) return true;
    if (x > y + tol) return false;
    return changeAmount(a.plan) < changeAmount(b.plan) - 1e-9;
  };

  let best = { plan: { guidance: { moves: [], startMinute: 0 }, vehicleExitShare: currentVehicleShare } as Plan, result: current };

  // ---------- 보행 안내 ----------
  const allowed = c.allowedDirections ? new Set(c.allowedDirections) : null;
  const candidates = junctionCandidates(input, maxDetour)
    .map((j) => ({ ...j, targets: j.targets.filter((d) => !allowed || allowed.has(d)) }))
    .filter((j) => j.targets.length > 0);
  const used = new Set<string>();
  for (let round = 0; round < allowedJunctions; round++) {
    let roundBest = best;
    let roundJunction: string | null = null;
    for (const cand of candidates) {
      if (used.has(cand.junctionNodeId)) continue;
      for (const ratios of ratioGrid(cand.targets.length, step, maxRatio)) {
        const moves: GuidanceMove[] = cand.targets
          .map((to, i) => ({ junctionNodeId: cand.junctionNodeId, from: cand.from, to, ratio: ratios[i]! }))
          .filter((m) => m.ratio > 0);
        if (!moves.length) continue;
        for (const startMinute of startMinutes) {
          const plan: Plan = { guidance: { moves: [...best.plan.guidance.moves, ...moves], startMinute }, vehicleExitShare: currentVehicleShare };
          const trial = { plan, result: run(plan) };
          if (better(trial, roundBest)) {
            roundBest = trial;
            roundJunction = cand.junctionNodeId;
          }
        }
      }
    }
    if (!roundJunction) break;
    best = roundBest;
    used.add(roundJunction);
  }

  // ---------- 차량 출구 배정 ----------
  const exits = input.vehicle?.parking.exits ?? [];
  if (input.vehicle && exits.length >= 2 && exits.length <= 4) {
    const vStep = c.vehicleShareStep ?? 0.05;
    const minShare = c.minVehicleExitShare ?? DEFAULT_MIN_VEHICLE_EXIT_SHARE;
    let vBest = best;
    for (const shares of compositions(exits.length, Math.round(1 / vStep))) {
      const share = Object.fromEntries(exits.map((e, i) => [e.id, Math.round(shares[i]! * vStep * 1e9) / 1e9]));
      if (Object.values(share).some((v) => v < minShare - 1e-9)) continue;
      const plan: Plan = { guidance: best.plan.guidance, vehicleExitShare: share };
      const trial = { plan, result: run(plan) };
      if (better(trial, vBest)) vBest = trial;
    }
    best = vBest;
  }

  return { current, best, evaluated };
}

interface JunctionCandidate {
  junctionNodeId: string;
  from: Direction;
  targets: Direction[];
}

/** 두 방면 이상이 같이 지나는 분기점마다, 가장 많이 가는 방면에서 나머지 방면으로 돌리는 후보를 만든다 */
function junctionCandidates(input: SimulationInput, maxDetour: number): JunctionCandidate[] {
  const blocked = new Set(input.blockedEdgeIds ?? []);
  const edgeById = new Map(input.network.edges.map((e) => [e.id, e]));
  const speed = input.assumptions.walkSpeedMPerMin;
  const junctions = input.network.nodes.filter((n) => n.kind === 'junction' || n.kind === 'bottleneck');

  // 출구별 방면 경로 (막힌 경로 제외)
  const byExit = new Map<string, Map<Direction, { nodes: Set<string>; walk: number }>>();
  for (const r of input.routes.routes)
    for (const p of r.paths) {
      if (p.edgeIds.some((id) => blocked.has(id) || !edgeById.has(id))) continue;
      const nodes = new Set<string>();
      let walk = 0;
      for (const id of p.edgeIds) {
        const e = edgeById.get(id)!;
        nodes.add(e.from);
        nodes.add(e.to);
        walk += e.lengthM / speed;
      }
      const m = byExit.get(p.exitNodeId) ?? new Map();
      m.set(r.direction, { nodes, walk });
      byExit.set(p.exitNodeId, m);
    }

  const out: JunctionCandidate[] = [];
  for (const j of junctions) {
    const dirs = new Set<Direction>();
    for (const m of byExit.values()) for (const [d, p] of m) if (p.nodes.has(j.id)) dirs.add(d);
    if (dirs.size < 2) continue;
    const list = [...dirs];
    const from = list.reduce((a, b) => ((input.directionShare[b] ?? 0) > (input.directionShare[a] ?? 0) ? b : a));
    const targets = list.filter((d) => {
      if (d === from) return false;
      for (const m of byExit.values()) {
        const f = m.get(from);
        const t = m.get(d);
        if (f && t && f.nodes.has(j.id) && t.nodes.has(j.id) && t.walk - f.walk > maxDetour) return false;
      }
      return true;
    });
    if (targets.length) out.push({ junctionNodeId: j.id, from, targets });
  }
  return out;
}

/** 방면 n개에 대한 비율 조합 (각 step 단위, 합 ≤ max) */
function* ratioGrid(n: number, step: number, max: number): Generator<number[]> {
  const units = Math.round(max / step);
  function* rec(i: number, left: number, acc: number[]): Generator<number[]> {
    if (i === n) {
      yield acc.map((u) => Math.round(u * step * 1e9) / 1e9);
      return;
    }
    for (let u = 0; u <= left; u++) yield* rec(i + 1, left - u, [...acc, u]);
  }
  yield* rec(0, units, []);
}

/** n칸에 total 단위를 나눠 담는 모든 방법 */
function* compositions(n: number, total: number): Generator<number[]> {
  function* rec(i: number, left: number, acc: number[]): Generator<number[]> {
    if (i === n - 1) {
      yield [...acc, left];
      return;
    }
    for (let u = 0; u <= left; u++) yield* rec(i + 1, left - u, [...acc, u]);
  }
  yield* rec(0, total, []);
}
