import { egressFractions } from './egress';
import { statusOf } from './status';
import { VehicleQueues, defaultExitShare, normalize as normalizeVehicleShare } from './vehicle';
import {
  DIRECTIONS,
  type Assumptions,
  type Direction,
  type EdgeSeries,
  type NetworkEdge,
  type SimulationInput,
  type SimulationResult,
  type Status,
} from './types';

/**
 * 1분 단위 보행 흐름 시뮬레이션.
 *
 * 시간 규칙: 배열의 t번째 값은 "종료 후 t분 ~ t+1분" 동안의 흐름이고, 대기 인원은 그 1분이 끝난 시점의 값이다.
 *
 * 모델
 * - 사람 무리(commodity) = 출구 × 방면. 각 무리는 routes.json의 길 토막 순서대로 이동한다.
 * - 길 토막마다 입구에 줄이 있다. 통과(t) = min(대기 + 도착, 용량), 대기(t) = 대기(t−1) + 도착(t) − 통과(t).
 * - 여러 무리가 같은 길을 쓰면 용량을 (대기 + 도착) 비율대로 나눈다.
 * - 통과한 사람은 길이 ÷ 보행 속도(분, 반올림)만큼 걸은 뒤 다음 길 토막 입구에 도착한다.
 * - 줄 번짐: 다음 길 토막의 대기가 대기 공간 한계(면적 ÷ 정체 시 1인당 면적)를 넘지 않도록 이번 길의 통과량을 줄인다.
 *   (걷는 중인 사람은 한계 계산에 넣지 않는 근사)
 */
export function simulate(input: SimulationInput): SimulationResult {
  const H = input.horizonMinutes ?? 90;
  const a = input.assumptions;
  const warnings: string[] = [];
  const blocked = new Set(input.blockedEdgeIds ?? []);

  const overrides = input.edgeWidthOverrides ?? {};
  const allEdges = input.network.edges.map((e) => (overrides[e.id] !== undefined ? { ...e, widthM: overrides[e.id]! } : e));
  const edgeById = new Map(allEdges.map((e) => [e.id, e]));
  const nodeById = new Map(input.network.nodes.map((n) => [n.id, n]));
  const weather = input.weather ? (a.weatherFactor[input.weather] ?? 1) : 1;

  // ---------- 출구 비율 ----------
  const exitIds = uniq(input.routes.routes.flatMap((r) => r.paths.map((p) => p.exitNodeId)));
  const exitShare = normalizeMap(
    Object.fromEntries(exitIds.map((id) => [id, nodeById.get(id)?.exitShare ?? 1])),
  );

  // ---------- 출구별 사용 가능한 경로 ----------
  type PathInfo = { direction: Direction; edgeIds: string[]; walkMinutesExact: number; nodes: Set<string> };
  const pathsByExit = new Map<string, Map<Direction, PathInfo>>();
  for (const route of input.routes.routes) {
    for (const p of route.paths) {
      for (const id of p.edgeIds) if (!edgeById.has(id)) throw new Error(`경로 ${route.direction}에 없는 길 토막 ${id}`);
      if (p.edgeIds.some((id) => blocked.has(id))) continue;
      const nodes = new Set<string>();
      let walk = 0;
      for (const id of p.edgeIds) {
        const e = edgeById.get(id)!;
        nodes.add(e.from);
        nodes.add(e.to);
        walk += e.lengthM / a.walkSpeedMPerMin;
      }
      const m = pathsByExit.get(p.exitNodeId) ?? new Map<Direction, PathInfo>();
      m.set(route.direction, { direction: route.direction, edgeIds: p.edgeIds, walkMinutesExact: walk, nodes });
      pathsByExit.set(p.exitNodeId, m);
    }
  }

  const requested = DIRECTIONS.filter((d) => (input.directionShare[d] ?? 0) > 0 || input.routes.routes.some((r) => r.direction === d));
  const availableAnywhere = new Set<Direction>();
  for (const m of pathsByExit.values()) for (const d of m.keys()) availableAnywhere.add(d);
  const unavailableDirections = requested.filter((d) => !availableAnywhere.has(d));
  if (availableAnywhere.size === 0) throw new Error('통제로 모든 방면이 막혔습니다');
  for (const d of unavailableDirections) warnings.push(`${d} 방면 경로가 막혀 계산에서 제외했습니다`);

  // ---------- 출구별 방면 비율 (안내 전 / 안내 후) ----------
  const compliance = clamp01(input.compliance ?? 1);
  const guidance = input.guidance;
  function sharesFor(exitId: string, guided: boolean): Partial<Record<Direction, number>> {
    const paths = pathsByExit.get(exitId);
    if (!paths) return {};
    const avail = [...paths.keys()];
    let s: Partial<Record<Direction, number>> = {};
    for (const d of avail) s[d] = Math.max(0, input.directionShare[d] ?? 0);
    const sum = avail.reduce((acc, d) => acc + (s[d] ?? 0), 0);
    if (sum <= 0) for (const d of avail) s[d] = 1 / avail.length;
    else for (const d of avail) s[d] = (s[d] ?? 0) / sum;
    if (guided && guidance) {
      const base = { ...s };
      for (const mv of guidance.moves) {
        const from = paths.get(mv.from);
        const to = paths.get(mv.to);
        if (!from || !to) continue;
        if (!from.nodes.has(mv.junctionNodeId) || !to.nodes.has(mv.junctionNodeId)) continue;
        const moved = Math.min(s[mv.from] ?? 0, (base[mv.from] ?? 0) * clamp01(mv.ratio) * compliance);
        s[mv.from] = (s[mv.from] ?? 0) - moved;
        s[mv.to] = (s[mv.to] ?? 0) + moved;
      }
    }
    return s;
  }
  if (guidance) {
    for (const mv of guidance.moves) {
      const ok = [...pathsByExit.values()].some(
        (m) => m.get(mv.from)?.nodes.has(mv.junctionNodeId) && m.get(mv.to)?.nodes.has(mv.junctionNodeId),
      );
      if (!ok) warnings.push(`안내(${mv.from}→${mv.to}, ${mv.junctionNodeId})는 두 경로가 그 분기점을 같이 지나지 않아 무시했습니다`);
    }
  }

  // ---------- 차량 (차로 가는 관객은 지하철 방면 보행에서 뺀다) ----------
  const fractions = egressFractions(input.egress, H);
  const veh = input.vehicle;
  let pedestrianAttendance = input.attendance;
  let vehicleQueues: VehicleQueues | null = null;
  let vehicles = 0;
  let occupancy = a.vehicleOccupancy;
  if (veh) {
    occupancy = veh.occupancy ?? a.vehicleOccupancy;
    const ratio = clamp01(veh.ratio);
    pedestrianAttendance = input.attendance * (1 - ratio);
    vehicles = (input.attendance * ratio) / occupancy;
    const lotCapacity = veh.parking.lots.reduce((s, l) => s + l.capacity, 0);
    if (vehicles > lotCapacity) {
      warnings.push(`예상 차량 ${Math.round(vehicles)}대가 주차 가능 대수 ${lotCapacity}대를 넘어 ${lotCapacity}대로 계산했습니다`);
      vehicles = lotCapacity;
    }
    const lag = Math.max(0, Math.round(a.walkToLotMinutes));
    const vDepart = Array.from({ length: H }, (_, t) => (t - lag >= 0 ? fractions[t - lag]! * vehicles : 0));
    const share = veh.exitShare ? normalizeVehicleShare(veh.exitShare, veh.parking.exits) : defaultExitShare(veh.parking);
    vehicleQueues = new VehicleQueues(vDepart, veh.parking.exits, share);
  }

  // ---------- 사람 무리 ----------
  const departures = fractions.map((f) => f * pedestrianAttendance);
  type Commodity = { exitId: string; direction: Direction; path: PathInfo; depart: number[] };
  const commodities: Commodity[] = [];
  for (const exitId of exitIds) {
    const paths = pathsByExit.get(exitId);
    if (!paths) {
      if ((exitShare[exitId] ?? 0) > 0) warnings.push(`출구 ${exitId}에서 갈 수 있는 경로가 없습니다`);
      continue;
    }
    const before = sharesFor(exitId, false);
    const after = sharesFor(exitId, true);
    const startAt = guidance?.startMinute ?? 0;
    for (const [d, path] of paths) {
      const depart = departures.map((v, t) => v * (exitShare[exitId] ?? 0) * ((t >= startAt ? after[d] : before[d]) ?? 0));
      commodities.push({ exitId, direction: d, path, depart });
    }
  }

  // ---------- 길 토막 준비 ----------
  const usedEdgeIds = uniq(commodities.flatMap((c) => c.path.edgeIds));
  const order = topoOrder(usedEdgeIds.map((id) => edgeById.get(id)!));
  const cap = new Map(usedEdgeIds.map((id) => [id, capacityPerMin(edgeById.get(id)!, a) * weather]));
  const limit = new Map(usedEdgeIds.map((id) => [id, edgeById.get(id)!.queueAreaM2 / a.jamAreaPerPersonM2]));
  const delay = new Map(usedEdgeIds.map((id) => [id, Math.round(edgeById.get(id)!.lengthM / a.walkSpeedMPerMin)]));

  // 무리별 단계 도착 예정 인원: pending[k][step][t]. step = 경로상 길 토막 번호, 마지막 = 도착점
  const pending = commodities.map((c) => Array.from({ length: c.path.edgeIds.length + 1 }, () => new Float64Array(H)));
  const queue = commodities.map((c) => new Float64Array(c.path.edgeIds.length));
  const edgeCommodities = new Map<string, { k: number; step: number }[]>();
  commodities.forEach((c, k) =>
    c.path.edgeIds.forEach((id, step) => {
      const list = edgeCommodities.get(id) ?? [];
      list.push({ k, step });
      edgeCommodities.set(id, list);
    }),
  );

  // 지도에 모든 길을 그리기 위해 쓰이지 않거나 막힌 길도 0으로 채운다
  const series: Record<string, EdgeSeries> = {};
  for (const e of allEdges) {
    const id = e.id;
    series[id] = {
      edgeId: id,
      capacityPerMin: blocked.has(id) ? 0 : capacityPerMin(e, a) * weather,
      arrivals: zeros(H),
      passed: zeros(H),
      queue: zeros(H),
      saturation: zeros(H),
      areaPerPersonM2: Array(H).fill(null),
      status: Array<Status>(H).fill('여유'),
    };
  }
  const prevQueue = new Map(usedEdgeIds.map((id) => [id, 0]));

  const ledger = { departed: zeros(H), arrived: zeros(H), inTransit: zeros(H), queued: zeros(H) };
  const transitAdd = zeros(H);
  const transitRemove = zeros(H + 1);
  let departedCum = 0;
  let arrivedCum = 0;
  let transitNow = 0;

  // ---------- 보행·차량 교차점: 1분을 시간 비율로 나눠 쓴다 (직전 분에 상대 흐름이 있었으면 적용) ----------
  const pedShare = clamp01(a.crossingPedestrianShare);
  const exitsByCrossing = new Map<string, string[]>();
  for (const ex of veh?.parking.exits ?? [])
    for (const cn of ex.crossingNodeIds) exitsByCrossing.set(cn, [...(exitsByCrossing.get(cn) ?? []), ex.id]);
  const pedEdgesByCrossing = new Map<string, string[]>();
  for (const id of usedEdgeIds) {
    const from = edgeById.get(id)!.from;
    if (exitsByCrossing.has(from)) pedEdgesByCrossing.set(from, [...(pedEdgesByCrossing.get(from) ?? []), id]);
  }
  const pedFactor = (id: string, t: number) => {
    const exits = exitsByCrossing.get(edgeById.get(id)!.from);
    if (!vehicleQueues || !exits) return 1;
    return exits.some((x) => vehicleQueues!.activeAt(x, t - 1)) ? pedShare : 1;
  };
  const vehicleFactor = (exitId: string, t: number) => {
    const ex = veh!.parking.exits.find((e) => e.id === exitId)!;
    const pedActive = ex.crossingNodeIds.some((cn) =>
      (pedEdgesByCrossing.get(cn) ?? []).some((id) => t > 0 && series[id]!.arrivals[t - 1]! + series[id]!.queue[t - 1]! > 1e-9),
    );
    return pedActive ? 1 - pedShare : 1;
  };

  // ---------- 1분씩 진행 ----------
  for (let t = 0; t < H; t++) {
    commodities.forEach((c, k) => {
      pending[k]![0]![t]! += c.depart[t]!;
      departedCum += c.depart[t]!;
    });

    // 이번 분에 각 길 토막이 받아들일 수 있는 남은 인원 (줄 번짐)
    const allowance = new Map(usedEdgeIds.map((id) => [id, limit.get(id)! + cap.get(id)! - prevQueue.get(id)!]));

    for (const id of order) {
      const members = edgeCommodities.get(id) ?? [];
      const c = cap.get(id)! * pedFactor(id, t);
      let arrTotal = 0;
      const demand: number[] = [];
      for (const { k, step } of members) {
        const arr = pending[k]![step]![t]!;
        arrTotal += arr;
        demand.push(queue[k]![step]! + arr);
      }
      const demandTotal = demand.reduce((x, y) => x + y, 0);
      const passTotal = Math.min(demandTotal, c);
      const pass = demand.map((d) => (demandTotal > 0 ? (passTotal * d) / demandTotal : 0));

      // 줄 번짐: 다음 길 토막별로 묶어 남은 수용량을 넘지 않게 줄인다
      const byNext = new Map<string, number[]>();
      members.forEach(({ k, step }, i) => {
        const next = commodities[k]!.path.edgeIds[step + 1];
        if (!next) return;
        const list = byNext.get(next) ?? [];
        list.push(i);
        byNext.set(next, list);
      });
      for (const [next, idxs] of byNext) {
        const want = idxs.reduce((s, i) => s + pass[i]!, 0);
        const room = Math.max(0, allowance.get(next)!);
        if (want > room) {
          const f = want > 0 ? room / want : 0;
          for (const i of idxs) pass[i] = pass[i]! * f;
        }
        allowance.set(next, room - Math.min(want, room));
      }

      let passedTotal = 0;
      let queueTotal = 0;
      members.forEach(({ k, step }, i) => {
        const p = pass[i]!;
        const left = demand[i]! - p;
        // 비율 나눗셈의 부동소수점 찌꺼기는 0으로 본다
        queue[k]![step] = left > 1e-9 ? left : 0;
        queueTotal += queue[k]![step]!;
        passedTotal += p;
        const d = delay.get(id)!;
        const arriveAt = t + d;
        if (d > 0) {
          transitAdd[t]! += p;
          if (arriveAt < H) transitRemove[arriveAt]! += p;
        }
        // 계산 범위를 넘어 도착하는 사람은 끝까지 "이동 중"으로 남는다
        if (arriveAt < H) pending[k]![step + 1]![arriveAt]! += p;
      });

      const s = series[id]!;
      s.arrivals[t] = arrTotal;
      s.passed[t] = passedTotal;
      s.queue[t] = queueTotal;
      s.saturation[t] = c > 0 ? arrTotal / c : arrTotal > 0 ? Infinity : 0;
      const area = edgeById.get(id)!.queueAreaM2;
      s.areaPerPersonM2[t] = queueTotal > 0 ? area / queueTotal : null;
      s.status[t] = statusOf({
        saturation: s.saturation[t]!,
        queue: queueTotal,
        areaPerPersonM2: s.areaPerPersonM2[t]!,
        jamAreaPerPersonM2: a.jamAreaPerPersonM2,
      });
    }

    // 걸어서 도착점에 닿은 사람
    commodities.forEach((c, k) => {
      const last = c.path.edgeIds.length;
      const v = pending[k]![last]![t]!;
      if (v) {
        arrivedCum += v;
        pending[k]![last]![t] = 0;
      }
    });

    vehicleQueues?.step(t, (exitId) => vehicleFactor(exitId, t));

    transitNow += transitAdd[t]! - transitRemove[t]!;
    for (const id of usedEdgeIds) prevQueue.set(id, series[id]!.queue[t]!);
    ledger.departed[t] = departedCum;
    ledger.arrived[t] = arrivedCum;
    ledger.inTransit[t] = transitNow;
    ledger.queued[t] = usedEdgeIds.reduce((s, id) => s + series[id]!.queue[t]!, 0);
  }

  // ---------- 합계 지표 ----------
  const queueTotal = Array.from({ length: H }, (_, t) => usedEdgeIds.reduce((s, id) => s + series[id]!.queue[t]!, 0));
  let maxQueue = 0;
  let maxQueueEdgeId: string | null = null;
  let maxQueueIndex = -1;
  for (const id of usedEdgeIds) {
    series[id]!.queue.forEach((q, t) => {
      if (q > maxQueue + 1e-9) {
        maxQueue = q;
        maxQueueEdgeId = id;
        maxQueueIndex = t;
      }
    });
  }
  const queueMinutesTotal = queueTotal.reduce((x, y) => x + y, 0);
  let peakQueueTotal = 0;
  let peakIndex = -1;
  queueTotal.forEach((q, t) => {
    if (q > peakQueueTotal + 1e-9) {
      peakQueueTotal = q;
      peakIndex = t;
    }
  });

  let detourMinutesTotal = 0;
  const directionPeople: Record<Direction, number> = { A: 0, B: 0, C: 0 };
  for (const c of commodities) {
    const people = c.depart.reduce((x, y) => x + y, 0);
    directionPeople[c.direction] += people;
    const shortest = Math.min(...[...pathsByExit.get(c.exitId)!.values()].map((p) => p.walkMinutesExact));
    detourMinutesTotal += people * (c.path.walkMinutesExact - shortest);
  }
  const totalPeople = DIRECTIONS.reduce((s, d) => s + directionPeople[d], 0);
  const directionShareActual: Record<Direction, number> = {
    A: totalPeople ? directionPeople.A / totalPeople : 0,
    B: totalPeople ? directionPeople.B / totalPeople : 0,
    C: totalPeople ? directionPeople.C / totalPeople : 0,
  };

  const EPS = 1e-6;
  let lastPositive = -1;
  queueTotal.forEach((q, t) => {
    if (q > EPS) lastPositive = t;
  });
  const clearMinute = lastPositive === -1 ? 0 : lastPositive === H - 1 ? null : lastPositive + 2;
  if (clearMinute === null) warnings.push(`계산 범위 ${H}분 안에 대기열이 다 풀리지 않습니다`);

  const vehicleDone = vehicleQueues?.finish() ?? null;
  const vehicleQueuePersonMinutes = vehicleDone ? vehicleDone.queueVehicleMinutes * occupancy : 0;
  const burden = queueMinutesTotal + detourMinutesTotal + vehicleQueuePersonMinutes;

  return {
    minutes: H,
    departures,
    edges: series,
    queueTotal,
    vehicle: vehicleDone ? { exits: vehicleDone.exits, occupancy, vehicles } : null,
    totals: {
      maxQueue,
      maxQueueEdgeId,
      maxQueueMinute: maxQueueIndex + 1,
      peakQueueTotal,
      peakQueueTotalMinute: peakIndex + 1,
      queueMinutesTotal,
      detourMinutesTotal,
      vehicleQueuePersonMinutes,
      burdenPersonMinutes: burden,
      perPersonExtraMinutes: input.attendance > 0 ? burden / input.attendance : 0,
      clearMinute,
      directionShareActual,
      directionPeople,
    },
    unavailableDirections,
    warnings,
    ledger,
  };
}

/** 길 토막 1분 통과 인원 = 유효 폭 × 폭 1m당 통과 인원 (계단은 평지의 stairsFactor배) */
export function capacityPerMin(e: NetworkEdge, a: Assumptions): number {
  const f = a.flowPerMeterPerMin;
  const perMeter = e.type === 'stairs' ? f.flat * f.stairsFactor : e.type === 'gate' ? f.gate : e.type === 'ramp' ? f.ramp : f.flat;
  return Math.max(0, e.widthM) * perMeter;
}

function topoOrder(edges: NetworkEdge[]): string[] {
  // 길 토막 e1 → e2 (e1.to === e2.from) 순서. 같은 분에 0분 거리로 이어지는 흐름이 상류부터 처리되도록.
  const indeg = new Map(edges.map((e) => [e.id, 0]));
  const out = new Map<string, string[]>();
  for (const e1 of edges)
    for (const e2 of edges)
      if (e1.to === e2.from) {
        out.set(e1.id, [...(out.get(e1.id) ?? []), e2.id]);
        indeg.set(e2.id, indeg.get(e2.id)! + 1);
      }
  const q = edges.filter((e) => indeg.get(e.id) === 0).map((e) => e.id);
  const res: string[] = [];
  while (q.length) {
    const id = q.shift()!;
    res.push(id);
    for (const n of out.get(id) ?? []) {
      indeg.set(n, indeg.get(n)! - 1);
      if (indeg.get(n) === 0) q.push(n);
    }
  }
  if (res.length !== edges.length) throw new Error('길 연결에 순환이 있습니다. from→to는 나가는 방향이어야 합니다');
  return res;
}

function normalizeMap(m: Record<string, number>): Record<string, number> {
  const sum = Object.values(m).reduce((x, y) => x + Math.max(0, y), 0);
  const keys = Object.keys(m);
  return Object.fromEntries(keys.map((k) => [k, sum > 0 ? Math.max(0, m[k]!) / sum : 1 / keys.length]));
}

const uniq = <T,>(xs: T[]) => [...new Set(xs)];
const zeros = (n: number) => Array<number>(n).fill(0);
const clamp01 = (x: number) => Math.min(1, Math.max(0, x));
