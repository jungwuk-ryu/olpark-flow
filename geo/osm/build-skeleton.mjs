// OSM 보행로 → network.json / routes.json 뼈대 (시연용 가정값)
import { readFileSync, writeFileSync } from 'node:fs';
const [, , osmPath, outDir] = process.argv;
const els = JSON.parse(readFileSync(osmPath, 'utf8')).elements;

const R = 6371000;
const rad = (d) => (d * Math.PI) / 180;
const dist = (a, b) => {
  const dLat = rad(b[1] - a[1]);
  const dLon = rad(b[0] - a[0]);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(rad(a[1])) * Math.cos(rad(b[1])) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
};
const WALK = new Set(['footway', 'path', 'pedestrian', 'steps', 'living_street', 'residential', 'service', 'cycleway']);
// 대규모 귀가 동선은 넓은 주 보행로를 탄다: 길 종류별 비용 배수 (클수록 피함)
const COST = { pedestrian: 0.8, footway: 1.0, cycleway: 1.0, service: 1.3, living_street: 1.3, residential: 1.5, steps: 1.5, path: 2.5 };
const UNPAVED = /^(dirt|ground|gravel|grass|earth|mud|sand|unpaved|fine_gravel|compacted|wood)$/;
const costOf = (t) => {
  let c = COST[t.highway] ?? 2;
  // 주차장 안 통로·건물 진입로는 귀가 동선이 아님 (연결이 끊기지 않게 제외 대신 큰 비용)
  if (t.highway === 'service' && /^(parking_aisle|drive-through)$/.test(t.service ?? '')) c = 5;
  // 공원 안 driveway는 가로수 산책로를 겸하는 경우가 많아 약한 벌점만
  if (t.highway === 'service' && t.service === 'driveway') c = 1.6;
  if (UNPAVED.test(t.surface ?? '')) c *= 1.6;
  const w = parseFloat(t.width);
  if (Number.isFinite(w) && w >= 5) c *= 0.9;
  return c;
};

// ---------- 그래프 ----------
const coord = new Map();
const adj = new Map();
const wayInfo = new Map();
const addEdge = (a, b, len, wayId) => {
  if (!adj.has(a)) adj.set(a, []);
  adj.get(a).push({ to: b, len, wayId });
};
for (const w of els.filter((e) => e.type === 'way' && WALK.has(e.tags?.highway))) {
  if (w.tags.access === 'private' || w.tags.foot === 'no') continue;

  wayInfo.set(w.id, w.tags);
  w.nodes.forEach((id, i) => coord.set(id, [w.geometry[i].lon, w.geometry[i].lat]));
  for (let i = 1; i < w.nodes.length; i++) {
    const a = w.nodes[i - 1];
    const b = w.nodes[i];
    const len = dist(coord.get(a), coord.get(b));
    addEdge(a, b, len, w.id);
    addEdge(b, a, len, w.id);
  }
}
const nearest = (p) => {
  let best = null;
  let bd = Infinity;
  for (const [id, c] of coord) {
    if (!adj.has(id)) continue;
    const d = dist(p, c);
    if (d < bd) {
      bd = d;
      best = id;
    }
  }
  return { id: best, d: bd };
};

// ---------- 도착점: 역 출입구 ----------
const entrances = els.filter((e) => e.tags?.railway === 'subway_entrance');
const pick = (lat, lon, ref) =>
  entrances.filter((e) => e.tags.ref === ref).sort((a, b) => dist([a.lon, a.lat], [lon, lat]) - dist([b.lon, b.lat], [lon, lat]))[0];
const DEST = [
  { direction: 'A', name: '올림픽공원역 3번 출구', line: '5·9호선', e: pick(37.5163, 127.131, '3') },
  { direction: 'B', name: '한성백제역 2번 출구', line: '9호선', e: pick(37.5164, 127.1162, '2') },
  { direction: 'C', name: '몽촌토성역 1번 출구', line: '8호선', e: pick(37.5175, 127.113, '1') },
];
for (const d of DEST) {
  const n = nearest([d.e.lon, d.e.lat]);
  d.graphNode = n.id;
  d.snapM = n.d;
}

// ---------- 출구: KSPO DOME(올림픽체조경기장) 둘레에서 가장 가까운 보행로 점 ----------
const dome = els.find((e) => e.type === 'way' && e.tags?.name === '올림픽체조경기장');
const ring = dome.geometry.map((p) => [p.lon, p.lat]);
const center = [ring.reduce((s, p) => s + p[0], 0) / ring.length, ring.reduce((s, p) => s + p[1], 0) / ring.length];
const distToRing = (p) => Math.min(...ring.map((q) => dist(p, q)));
const SIDES = [
  { key: 'south', name: 'KSPO DOME 남측 출구', bearing: 180 },
  { key: 'east', name: 'KSPO DOME 동측 출구', bearing: 90 },
  { key: 'west', name: 'KSPO DOME 서측 출구', bearing: 270 },
];
const bearingOf = (p) => {
  const y = Math.sin(rad(p[0] - center[0])) * Math.cos(rad(p[1]));
  const x = Math.cos(rad(center[1])) * Math.sin(rad(p[1])) - Math.sin(rad(center[1])) * Math.cos(rad(p[1])) * Math.cos(rad(p[0] - center[0]));
  return ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360;
};
const angDiff = (a, b) => Math.abs(((a - b + 540) % 360) - 180);
const exits = [];
for (const s of SIDES) {
  let best = null;
  let bd = Infinity;
  for (const [id, c] of coord) {
    if (!adj.has(id) || angDiff(bearingOf(c), s.bearing) > 35) continue;
    const d = distToRing(c);
    if (d < bd) {
      bd = d;
      best = id;
    }
  }
  if (best && bd < 60) exits.push({ ...s, graphNode: best, gapM: bd });
}

// ---------- 최단 경로 (계단에는 약간의 벌점) ----------
function dijkstra(src) {
  const D = new Map([[src, 0]]);
  const P = new Map();
  const done = new Set();
  const pq = [[0, src]];
  while (pq.length) {
    pq.sort((a, b) => a[0] - b[0]);
    const [d, u] = pq.shift();
    if (done.has(u)) continue;
    done.add(u);
    for (const e of adj.get(u) ?? []) {
      const pen = costOf(wayInfo.get(e.wayId) ?? {});
      const nd = d + e.len * pen;
      if (nd < (D.get(e.to) ?? Infinity)) {
        D.set(e.to, nd);
        P.set(e.to, { from: u, wayId: e.wayId });
        pq.push([nd, e.to]);
      }
    }
  }
  return { D, P };
}
// 방면별 경유점: 항공영상(브이월드)으로 확인한 주 보행 동선 (광장·넓은 산책로)
// A: 체조경기장 남동쪽 광장 → 올림픽공원역 방면 넓은 보행로
// B·C: 핸드볼경기장 북쪽 광장 → 중앙 산책로 → 몽촌해자 남쪽 → (B) 남쪽 출구 / (C) 평화의광장
const VIA = {
  A: [[127.12809, 37.51784], [127.13088, 37.51665]],
  B: [[127.12605, 37.51806], [127.12407, 37.51763], [127.12187, 37.5178], [127.11736, 37.51744], [127.1165, 37.51736]],
  C: [[127.12605, 37.51806], [127.12407, 37.51763], [127.12187, 37.5178], [127.11736, 37.51744], [127.11618, 37.51814], [127.11457, 37.51772]],
};
const viaNodes = Object.fromEntries(Object.entries(VIA).map(([k, v]) => [k, v.map((p) => nearest(p).id)]));
const dj = new Map();
const djOf = (n) => (dj.has(n) ? dj.get(n) : (dj.set(n, dijkstra(n)), dj.get(n)));
const legPath = (a, b) => {
  const { D, P } = djOf(a);
  if (!D.has(b)) return null;
  const nodes = [b];
  const ways = [];
  let cur = b;
  while (cur !== a) {
    const p = P.get(cur);
    ways.unshift(p.wayId);
    cur = p.from;
    nodes.unshift(cur);
  }
  return { nodes, ways };
};
const paths = [];
for (const ex of exits) {
  for (const d of DEST) {
    const stops = [ex.graphNode, ...viaNodes[d.direction], d.graphNode];
    let nodes = [ex.graphNode];
    let ways = [];
    let missing = false;
    for (let i = 1; i < stops.length; i++) {
      if (stops[i] === stops[i - 1]) continue;
      const leg = legPath(stops[i - 1], stops[i]);
      if (!leg) { missing = true; break; }
      nodes = nodes.concat(leg.nodes.slice(1));
      ways = ways.concat(leg.ways);
    }
    // 되돌아가는 구간(같은 점을 두 번 지남) 제거
    const seen = new Map();
    for (let i = 0; i < nodes.length; i++) {
      if (seen.has(nodes[i])) {
        const j = seen.get(nodes[i]);
        nodes.splice(j + 1, i - j);
        ways.splice(j, i - j);
        i = j;
        for (const [k, v] of [...seen]) if (v > j) seen.delete(k);
      } else seen.set(nodes[i], i);
    }
    let lengthM = 0;
    for (let i = 1; i < nodes.length; i++) lengthM += dist(coord.get(nodes[i - 1]), coord.get(nodes[i]));
    paths.push(missing ? { exit: ex, dest: d, missing: true } : { exit: ex, dest: d, nodes, ways, lengthM });
  }
}
const ok = paths.filter((p) => !p.missing);

// ---------- 핵심 점 ----------
const usedNext = new Map();
const usedPrev = new Map();
const addSet = (m, k, v) => {
  if (!m.has(k)) m.set(k, new Set());
  m.get(k).add(v);
};
for (const p of ok)
  for (let i = 1; i < p.nodes.length; i++) {
    addSet(usedNext, p.nodes[i - 1], p.nodes[i]);
    addSet(usedPrev, p.nodes[i], p.nodes[i - 1]);
  }
const kindOfWay = (wid) => (wayInfo.get(wid)?.highway === 'steps' ? 'stairs' : 'flat');
const key = new Map();
for (const ex of exits) key.set(ex.graphNode, 'exit');
for (const d of DEST) key.set(d.graphNode, 'destination');
for (const [n, s] of usedNext) if (s.size > 1 && !key.has(n)) key.set(n, 'junction');
for (const [n, s] of usedPrev) if (s.size > 1 && !key.has(n)) key.set(n, 'junction');
for (const p of ok)
  for (let i = 1; i < p.ways.length; i++) {
    const n = p.nodes[i];
    if (kindOfWay(p.ways[i - 1]) !== kindOfWay(p.ways[i]) && !key.has(n)) key.set(n, 'bottleneck');
  }
// 긴 토막은 220m 안팎으로 나눠 폭 측정 단위로 쓴다
for (const p of ok) {
  let acc = 0;
  for (let i = 1; i < p.nodes.length; i++) {
    acc += dist(coord.get(p.nodes[i - 1]), coord.get(p.nodes[i]));
    if (key.has(p.nodes[i])) {
      acc = 0;
      continue;
    }
    if (acc > 220) {
      key.set(p.nodes[i], 'bottleneck');
      acc = 0;
    }
  }
}

// ---------- 토막 ----------
const DEFAULT_WIDTH = { cycleway: 3, footway: 3, path: 2.5, pedestrian: 8, steps: 3, living_street: 4, residential: 3, service: 4 };
const r5 = (x) => Math.round(x * 1e5) / 1e5;
const edges = new Map();
const routeEdges = [];
for (const p of ok) {
  const list = [];
  let start = 0;
  for (let i = 1; i < p.nodes.length; i++) {
    if (!key.has(p.nodes[i])) continue;
    const a = p.nodes[start];
    const b = p.nodes[i];
    const k = `${a}>${b}`;
    if (!edges.has(k)) {
      const seg = p.nodes.slice(start, i + 1);
      const segWays = p.ways.slice(start, i);
      let lengthM = 0;
      for (let j = 1; j < seg.length; j++) lengthM += dist(coord.get(seg[j - 1]), coord.get(seg[j]));
      const hw = segWays.map((w) => wayInfo.get(w)?.highway);
      const stairs = hw.includes('steps');
      const widths = segWays.map((w) => parseFloat(wayInfo.get(w)?.width)).filter((x) => Number.isFinite(x));
      const tagW = widths.length ? Math.min(...widths) : null;
      const counts = {};
      hw.forEach((h) => (counts[h] = (counts[h] ?? 0) + 1));
      const mainHw = Object.entries(counts).sort((x, y) => y[1] - x[1])[0][0];
      const widthM = tagW ?? (stairs ? DEFAULT_WIDTH.steps : (DEFAULT_WIDTH[mainHw] ?? 3));
      const names = [...new Set(segWays.map((w) => wayInfo.get(w)?.name).filter(Boolean))];
      edges.set(k, {
        a,
        b,
        lengthM: Math.round(lengthM),
        widthM,
        type: stairs ? 'stairs' : 'flat',
        queueAreaM2: Math.round(widthM * Math.min(40, Math.max(10, lengthM))),
        stepFree: !stairs,
        path: seg.map((n) => coord.get(n).map(r5)),
        widthFromOsm: tagW !== null,
        highway: mainHw,
        names,
      });
    }
    list.push(k);
    start = i;
  }
  routeEdges.push({ p, list });
}

// ---------- ID·이름 ----------
const usedNodes = new Set([...edges.values()].flatMap((e) => [e.a, e.b]));
const nodeId = new Map();
const nodeOut = [];
let jn = 0;
let bn = 0;
for (const [n, kind] of key) {
  if (!usedNodes.has(n)) continue;
  let id;
  let name;
  let extra = {};
  if (kind === 'exit') {
    const ex = exits.find((x) => x.graphNode === n);
    id = `n-kspo-exit-${ex.key}`;
    name = ex.name;
    extra = { venueId: 'kspo-dome', exitShare: 0 };
  } else if (kind === 'destination') {
    const d = DEST.find((x) => x.graphNode === n);
    id = `n-dest-${d.direction.toLowerCase()}`;
    name = d.name;
    extra = { direction: d.direction };
  } else if (kind === 'junction') {
    jn++;
    id = `n-junction-${jn}`;
    name = `분기점 ${jn}`;
  } else {
    bn++;
    id = `n-point-${bn}`;
    name = `지점 ${bn}`;
  }
  nodeId.set(n, id);
  nodeOut.push({ id, kind, name, lngLat: coord.get(n).map(r5), ...extra, source: '가정' });
}
const exitNodes = nodeOut.filter((n) => n.kind === 'exit');
exitNodes.forEach((n, i) => (n.exitShare = i < exitNodes.length - 1 ? Math.round(1000 / exitNodes.length) / 1000 : 0));
exitNodes[exitNodes.length - 1].exitShare = Math.round((1 - exitNodes.slice(0, -1).reduce((s, n) => s + n.exitShare, 0)) * 1000) / 1000;

const edgeOut = [];
const edgeId = new Map();
let en = 0;
for (const [k, e] of edges) {
  en++;
  const id = `e-${String(en).padStart(2, '0')}`;
  edgeId.set(k, id);
  edgeOut.push({
    id,
    from: nodeId.get(e.a),
    to: nodeId.get(e.b),
    lengthM: e.lengthM,
    widthM: e.widthM,
    type: e.type,
    queueAreaM2: e.queueAreaM2,
    stepFree: e.stepFree,
    path: e.path,
    source: '가정',
  });
}
const routes = DEST.map((d) => ({
  direction: d.direction,
  name: d.name,
  line: d.line,
  destinationNodeId: `n-dest-${d.direction.toLowerCase()}`,
  paths: routeEdges
    .filter((r) => r.p.dest === d)
    .map((r) => ({ exitNodeId: nodeId.get(r.p.exit.graphNode), edgeIds: r.list.map((k) => edgeId.get(k)) })),
}));

writeFileSync(
  `${outDir}/network.json`,
  JSON.stringify({ schemaVersion: 1, coordSystemOriginal: 'EPSG:4326 (OpenStreetMap)', source: '가정', nodes: nodeOut, edges: edgeOut }, null, 2),
);
writeFileSync(`${outDir}/routes.json`, JSON.stringify({ schemaVersion: 1, routes }, null, 2));

const csv = [
  'edgeId,from,to,lengthM,osmHighway,osmName,osmWidth,widthTotalM,obstaclesM,shyM,widthEffectiveM,areaM2,method,imageDate,confidence,checkLat,checkLng,kakaoMap,memo',
];
for (const [k, e] of edges) {
  const mid = e.path[Math.floor(e.path.length / 2)];
  csv.push(
    [edgeId.get(k), nodeId.get(e.a), nodeId.get(e.b), e.lengthM, e.highway, `"${e.names.join(' / ')}"`, e.widthFromOsm ? e.widthM : '', '', '', '', '', '', '', '', '', mid[1], mid[0], `https://map.kakao.com/link/map/${mid[1]},${mid[0]}`, ''].join(','),
  );
}
writeFileSync(`${outDir}/measurements.csv`, '﻿' + csv.join('\n'));

console.log(
  JSON.stringify(
    {
      destinations: DEST.map((d) => ({ dir: d.direction, entranceOsm: d.e.id, snapM: Math.round(d.snapM) })),
      exits: exits.map((e) => ({ key: e.key, gapM: Math.round(e.gapM) })),
      paths: paths.map((p) => ({
        exit: p.exit.key,
        dir: p.dest.direction,
        missing: !!p.missing,
        lengthM: Math.round(p.lengthM ?? 0),
        straightM: Math.round(dist(coord.get(p.exit.graphNode), [p.dest.e.lon, p.dest.e.lat])),
      })),
      nodes: nodeOut.length,
      kinds: nodeOut.reduce((m, n) => ((m[n.kind] = (m[n.kind] ?? 0) + 1), m), {}),
      edges: edgeOut.length,
      stairsEdges: edgeOut.filter((e) => e.type === 'stairs').length,
      widthFromOsm: [...edges.values()].filter((e) => e.widthFromOsm).length,
    },
    null,
    1,
  ),
);
