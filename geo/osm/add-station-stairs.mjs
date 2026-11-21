// 역마다 "출입구 계단" 토막을 끝에 붙인다 (가정: 폭 3m 계단, 20m, 대기 공간 150㎡)
import { readFileSync, writeFileSync } from 'node:fs';
const [, , dataDir] = process.argv;
const net = JSON.parse(readFileSync(`${dataDir}/network.json`, 'utf8'));
const routes = JSON.parse(readFileSync(`${dataDir}/routes.json`, 'utf8'));
for (const r of routes.routes) {
  const destId = r.destinationNodeId;
  const dest = net.nodes.find((n) => n.id === destId);
  const d = r.direction.toLowerCase();
  const frontId = `n-station-front-${d}`;
  const stairId = `e-station-${d}`;
  if (net.edges.some((e) => e.id === stairId)) continue;
  // 계단 앞 지점은 마지막 길 위, 도착점에서 20m쯤 떨어진 곳 (경도 방향으로 근사 이동하지 않고 마지막 path 점을 씀)
  const lastEdges = net.edges.filter((e) => e.to === destId);
  const ref = lastEdges[0].path[Math.max(0, lastEdges[0].path.length - 2)];
  net.nodes.push({ id: frontId, kind: 'bottleneck', name: `${r.name.replace(/ 출구$/, '')} 출입구 앞`, lngLat: ref, source: '가정' });
  for (const e of lastEdges) {
    e.to = frontId;
    if (e.path) e.path[e.path.length - 1] = ref;
  }
  net.edges.push({
    id: stairId,
    from: frontId,
    to: destId,
    lengthM: 20,
    widthM: 3,
    type: 'stairs',
    queueAreaM2: 150,
    stepFree: false,
    path: [ref, dest.lngLat],
    source: '가정',
  });
  for (const p of r.paths) p.edgeIds.push(stairId);
}
writeFileSync(`${dataDir}/network.json`, JSON.stringify(net, null, 2) + '\n');
writeFileSync(`${dataDir}/routes.json`, JSON.stringify(routes, null, 2) + '\n');
console.log('ok', net.nodes.length, net.edges.length);
