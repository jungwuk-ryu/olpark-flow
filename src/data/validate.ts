import { GENRES, type DataBundle } from './contract';

/** 올림픽공원 주변 좌표 범위 (넉넉하게) */
const LAT = [37.505, 37.535] as const;
const LNG = [127.1, 127.145] as const;
const EPS = 0.001;

/**
 * docs/data-contract.md 규칙 검사. 오류 문장 목록을 돌려준다 (비어 있으면 통과).
 * `pnpm validate:data`가 src/data의 실제 파일에 대해 이 함수를 돌린다.
 */
export function validateData(d: DataBundle): string[] {
  const err: string[] = [];
  const nodeIds = new Set(d.network.nodes.map((n) => n.id));
  const edgeById = new Map(d.network.edges.map((e) => [e.id, e]));
  const nodeById = new Map(d.network.nodes.map((n) => [n.id, n]));

  dupes(d.network.nodes.map((n) => n.id)).forEach((id) => err.push(`network: 장소 ID ${id}가 중복됩니다`));
  dupes(d.network.edges.map((e) => e.id)).forEach((id) => err.push(`network: 길 토막 ID ${id}가 중복됩니다`));

  // ---------- network ----------
  const inRange = ([lng, lat]: [number, number]) => lat >= LAT[0] && lat <= LAT[1] && lng >= LNG[0] && lng <= LNG[1];
  for (const n of d.network.nodes) {
    if (!inRange(n.lngLat)) err.push(`network: 장소 ${n.id}의 좌표 [${n.lngLat}]가 올림픽공원 근처가 아닙니다 ([경도, 위도] 순서인지 확인)`);
    if (n.kind === 'exit' && n.exitShare === undefined) err.push(`network: 출구 ${n.id}에 exitShare가 없습니다`);
    if (n.kind === 'destination' && !n.direction) err.push(`network: 도착점 ${n.id}에 direction이 없습니다`);
  }
  const byVenue = new Map<string, number>();
  for (const n of d.network.nodes)
    if (n.kind === 'exit') byVenue.set(n.venueId ?? '(공연장 없음)', (byVenue.get(n.venueId ?? '(공연장 없음)') ?? 0) + (n.exitShare ?? 0));
  for (const [v, s] of byVenue) if (Math.abs(s - 1) > EPS) err.push(`network: ${v} 출구 exitShare 합이 ${round(s)}입니다 (1이어야 함)`);

  for (const e of d.network.edges) {
    if (!nodeIds.has(e.from)) err.push(`network: 길 토막 ${e.id}의 from ${e.from}이 없는 장소입니다`);
    if (!nodeIds.has(e.to)) err.push(`network: 길 토막 ${e.id}의 to ${e.to}가 없는 장소입니다`);
    if (!(e.lengthM >= 0)) err.push(`network: 길 토막 ${e.id}의 lengthM이 0 이상이어야 합니다`);
    if (!(e.widthM > 0)) err.push(`network: 길 토막 ${e.id}의 widthM이 0보다 커야 합니다`);
    if (!(e.queueAreaM2 >= 0)) err.push(`network: 길 토막 ${e.id}의 queueAreaM2가 0 이상이어야 합니다`);
    for (const p of e.path ?? []) if (!inRange(p)) err.push(`network: 길 토막 ${e.id}의 path 좌표 [${p}]가 올림픽공원 근처가 아닙니다`);
  }

  // ---------- routes ----------
  for (const r of d.routes.routes) {
    const dest = nodeById.get(r.destinationNodeId);
    if (!dest) err.push(`routes: ${r.direction} 방면 도착점 ${r.destinationNodeId}가 없는 장소입니다`);
    else if (dest.direction !== r.direction) err.push(`routes: ${r.direction} 방면 도착점 ${dest.id}의 방면이 ${dest.direction ?? '없음'}입니다`);
    for (const p of r.paths) {
      const label = `routes: ${r.direction} 방면 (${p.exitNodeId} 출발)`;
      const missing = p.edgeIds.filter((id) => !edgeById.has(id));
      missing.forEach((id) => err.push(`${label} 경로에 없는 길 토막 ${id}`));
      if (missing.length || !p.edgeIds.length) continue;
      const es = p.edgeIds.map((id) => edgeById.get(id)!);
      if (es[0]!.from !== p.exitNodeId) err.push(`${label} 첫 길 토막이 출구 ${p.exitNodeId}에서 시작하지 않습니다`);
      for (let i = 1; i < es.length; i++)
        if (es[i - 1]!.to !== es[i]!.from) err.push(`${label} ${es[i - 1]!.id} → ${es[i]!.id}가 이어지지 않습니다`);
      if (es[es.length - 1]!.to !== r.destinationNodeId) err.push(`${label} 마지막 길 토막이 도착점에서 끝나지 않습니다`);
    }
  }

  // ---------- parking ----------
  const exitIds = new Set(d.parking.exits.map((e) => e.id));
  for (const l of d.parking.lots) {
    if (!(l.capacity >= 0)) err.push(`parking: ${l.id} capacity가 0 이상이어야 합니다`);
    if (!l.exitIds.length) err.push(`parking: ${l.id}에 연결된 출구가 없습니다`);
    for (const id of l.exitIds) if (!exitIds.has(id)) err.push(`parking: ${l.id}의 출구 ${id}가 exits에 없습니다`);
  }
  for (const x of d.parking.exits) {
    if (!(x.throughputPerMin > 0)) err.push(`parking: 출구 ${x.id} throughputPerMin이 0보다 커야 합니다`);
    for (const cn of x.crossingNodeIds) {
      const n = nodeById.get(cn);
      if (!n) err.push(`parking: 출구 ${x.id}의 교차점 ${cn}이 network에 없습니다`);
      else if (n.kind !== 'crossing') err.push(`parking: 출구 ${x.id}의 교차점 ${cn}의 kind가 crossing이 아닙니다`);
    }
  }

  // ---------- assumptions ----------
  const a = d.assumptions;
  const positive: [string, number][] = [
    ['walkSpeedMPerMin', a.walkSpeedMPerMin],
    ['flowPerMeterPerMin.flat', a.flowPerMeterPerMin.flat],
    ['flowPerMeterPerMin.gate', a.flowPerMeterPerMin.gate],
    ['jamAreaPerPersonM2', a.jamAreaPerPersonM2],
    ['vehicleOccupancy', a.vehicleOccupancy],
    ['staffPerJunction', a.staffPerJunction],
  ];
  for (const [k, v] of positive) if (!(v > 0)) err.push(`assumptions: ${k}는 0보다 커야 합니다`);
  if (!(a.crossingPedestrianShare >= 0 && a.crossingPedestrianShare <= 1)) err.push('assumptions: crossingPedestrianShare는 0~1이어야 합니다');

  // ---------- events / predictions ----------
  const eventIds = new Set(d.events.events.map((e) => e.id));
  for (const e of d.events.events) if (!(Date.parse(e.endAt) > Date.parse(e.startAt))) err.push(`events: ${e.id}의 endAt이 startAt보다 늦어야 합니다`);
  for (const p of d.predictions.predictions) {
    const label = `prediction ${p.eventId}:`;
    if (!eventIds.has(p.eventId)) err.push(`${label} events.json에 없는 공연입니다`);
    if (p.genre) {
      if (!GENRES.includes(p.genre.value)) err.push(`${label} genre.value ${p.genre.value}는 정해진 장르가 아닙니다`);
      if (!p.genre.sources.length) err.push(`${label} genre.sources가 1건 이상 있어야 합니다`);
    }
    if (p.attendance && !(p.attendance.low <= p.attendance.value && p.attendance.value <= p.attendance.high))
      err.push(`${label} attendance는 low ≤ value ≤ high여야 합니다`);
    if (p.egressCurve) {
      const c = p.egressCurve;
      if (!(c.peakMinute > 0 && c.peakMinute < c.totalMinutes)) err.push(`${label} egressCurve는 0 < peakMinute < totalMinutes여야 합니다`);
      if (!(c.totalMinutesLow <= c.totalMinutes && c.totalMinutes <= c.totalMinutesHigh))
        err.push(`${label} egressCurve는 totalMinutesLow ≤ totalMinutes ≤ totalMinutesHigh여야 합니다`);
    }
    if (p.directionShare) {
      const s = Object.values(p.directionShare).reduce((x, y) => x + (y ?? 0), 0);
      if (Math.abs(s - 1) > EPS) err.push(`${label} directionShare 합이 ${round(s)}입니다 (1이어야 함)`);
    }
    if (p.vehicle) {
      const v = p.vehicle;
      if (!(0 <= v.low && v.low <= v.ratio && v.ratio <= v.high && v.high <= 1)) err.push(`${label} vehicle은 0 ≤ low ≤ ratio ≤ high ≤ 1이어야 합니다`);
    }
    if (p.similarEvents.length !== 5) err.push(`${label} similarEvents는 정확히 5건이어야 합니다 (지금 ${p.similarEvents.length}건)`);
  }

  // ---------- notices ----------
  for (const n of d.notices.notices) {
    for (const id of n.edgeIds) if (!edgeById.has(id)) err.push(`notices: ${n.id}의 길 토막 ${id}가 network에 없습니다`);
    if (!n.evidence.quote.trim()) err.push(`notices: ${n.id}에 근거 문장(evidence.quote)이 없습니다`);
    if (n.effect === 'narrowed' && !(n.widthM !== null && n.widthM > 0)) err.push(`notices: ${n.id}는 narrowed이므로 widthM이 필요합니다`);
  }

  return err;
}

function dupes(xs: string[]): string[] {
  const seen = new Set<string>();
  const out = new Set<string>();
  for (const x of xs) (seen.has(x) ? out : seen).add(x);
  return [...out];
}
const round = (x: number) => Math.round(x * 1000) / 1000;
