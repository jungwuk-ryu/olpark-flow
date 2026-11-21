// 생성한 뼈대를 src/data에 반영: 교차점 후보 지정, 공지 길 ID 재연결
import { readFileSync, writeFileSync } from 'node:fs';
const [, , genDir, dataDir] = process.argv;
const net = JSON.parse(readFileSync(`${genDir}/network.json`, 'utf8'));
const routes = JSON.parse(readFileSync(`${genDir}/routes.json`, 'utf8'));
const R = 6371000, rad = (d) => (d * Math.PI) / 180;
const dist = (a, b) => { const dLat = rad(b[1] - a[1]), dLon = rad(b[0] - a[0]); const h = Math.sin(dLat / 2) ** 2 + Math.cos(rad(a[1])) * Math.cos(rad(b[1])) * Math.sin(dLon / 2) ** 2; return 2 * R * Math.asin(Math.sqrt(h)); };
const node = (id) => net.nodes.find((n) => n.id === id);
const edge = (id) => net.edges.find((e) => e.id === id);
const dirsOf = (eid) => new Set(routes.routes.filter((r) => r.paths.some((p) => p.edgeIds.includes(eid))).map((r) => r.direction));

// 교차점 후보: A 방면에만 쓰이는 중간 점 중 OSM "공원 주차장"(37.51621, 127.12887)에서 가장 가까운 점
const aOnly = new Set(net.edges.filter((e) => { const d = dirsOf(e.id); return d.size === 1 && d.has('A'); }).flatMap((e) => [e.from, e.to]));
const lot = [127.12887, 37.51621];
const crossing = net.nodes
  .filter((n) => aOnly.has(n.id) && (n.kind === 'bottleneck' || n.kind === 'junction'))
  .sort((a, b) => dist(a.lngLat, lot) - dist(b.lngLat, lot))[0];
const oldId = crossing.id;
crossing.id = 'n-crossing-a';
crossing.kind = 'crossing';
crossing.name = '주차장 진출로 교차점 (후보)';
for (const e of net.edges) { if (e.from === oldId) e.from = crossing.id; if (e.to === oldId) e.to = crossing.id; }

// 공지 1: C 방면에만 쓰이는 길 중 세계평화의문(37.51831, 127.11539)에서 가장 가까운 길
const gate = [127.11539, 37.51831];
const mid = (e) => e.path[Math.floor(e.path.length / 2)];
const cOnly = net.edges.filter((e) => { const d = dirsOf(e.id); return d.size === 1 && d.has('C'); });
const closed = cOnly.sort((a, b) => dist(mid(a), gate) - dist(mid(b), gate))[0];
// 공지 2: 남측 출구에서 나가는 길
const narrowed = net.edges.find((e) => e.from === 'n-kspo-exit-south');

const notices = JSON.parse(readFileSync(`${dataDir}/notices.json`, 'utf8'));
notices.notices[0].edgeIds = [closed.id];
notices.notices[0].segmentName = '세계평화의문 → 몽촌토성역 방면 보행로';
notices.notices[1].edgeIds = [narrowed.id];
notices.notices[1].widthM = Math.max(1, Math.round(narrowed.widthM / 2 * 10) / 10);
notices.notices[1].content = `푸드트럭 부스 설치로 유효 폭 ${narrowed.widthM}m → ${notices.notices[1].widthM}m`;

const parking = JSON.parse(readFileSync(`${dataDir}/parking.json`, 'utf8'));
for (const x of parking.exits) x.crossingNodeIds = x.id === 'gate-east-2' ? [crossing.id] : [];

writeFileSync(`${dataDir}/network.json`, JSON.stringify(net, null, 2) + '\n');
writeFileSync(`${dataDir}/routes.json`, JSON.stringify(routes, null, 2) + '\n');
writeFileSync(`${dataDir}/notices.json`, JSON.stringify(notices, null, 2) + '\n');
writeFileSync(`${dataDir}/parking.json`, JSON.stringify(parking, null, 2) + '\n');
console.log({ crossing: { from: oldId, at: crossing.lngLat }, closed: closed.id, narrowed: narrowed.id, narrowedTo: notices.notices[1].widthM });
