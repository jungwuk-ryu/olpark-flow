// network.json + osm.json → 확인용 SVG HTML
import { readFileSync, writeFileSync } from 'node:fs';
const [, , netPath, routesPath, osmPath, outPath] = process.argv;
const net = JSON.parse(readFileSync(netPath, 'utf8'));
const routes = JSON.parse(readFileSync(routesPath, 'utf8'));
const osm = JSON.parse(readFileSync(osmPath, 'utf8')).elements;
const W = 1200, H = 800;
const bbox = { minLon: 127.108, maxLon: 127.137, minLat: 37.508, maxLat: 37.527 };
const k = Math.cos((37.517 * Math.PI) / 180);
const sx = W / ((bbox.maxLon - bbox.minLon) * k), sy = H / (bbox.maxLat - bbox.minLat);
const s = Math.min(sx, sy);
const X = (lon) => (lon - bbox.minLon) * k * s, Y = (lat) => H - (lat - bbox.minLat) * s;
const color = { A: '#2F6E60', B: '#C9A26B', C: '#8EA2C0' };
let bg = '';
for (const w of osm.filter((e) => e.type === 'way' && e.geometry)) {
  const d = w.geometry.map((p, i) => `${i ? 'L' : 'M'}${X(p.lon).toFixed(1)} ${Y(p.lat).toFixed(1)}`).join('');
  const isB = w.tags?.building;
  bg += `<path d="${d}" fill="${isB ? '#cfd8ca' : 'none'}" stroke="${isB ? '#9aa' : '#d6dbd3'}" stroke-width="${isB ? 1 : 1}"/>`;
}
const edgeDir = {};
for (const r of routes.routes) for (const p of r.paths) for (const id of p.edgeIds) (edgeDir[id] ??= new Set()).add(r.direction);
let fg = '';
for (const e of net.edges) {
  const d = e.path.map((p, i) => `${i ? 'L' : 'M'}${X(p[0]).toFixed(1)} ${Y(p[1]).toFixed(1)}`).join('');
  const dirs = [...(edgeDir[e.id] ?? [])];
  const c = dirs.length === 1 ? color[dirs[0]] : '#1F2A27';
  fg += `<path d="${d}" fill="none" stroke="${c}" stroke-width="4" stroke-linecap="round" opacity="0.85"/>`;
  const m = e.path[Math.floor(e.path.length / 2)];
  fg += `<text x="${X(m[0]) + 4}" y="${Y(m[1]) - 4}" font-size="11" fill="#333">${e.id} ${e.lengthM}m</text>`;
}
for (const n of net.nodes) {
  const c = n.kind === 'exit' ? '#E07B39' : n.kind === 'destination' ? '#C8423B' : n.kind === 'junction' ? '#1F2A27' : '#888';
  fg += `<circle cx="${X(n.lngLat[0])}" cy="${Y(n.lngLat[1])}" r="${n.kind === 'exit' || n.kind === 'destination' ? 7 : 4}" fill="${c}" stroke="#fff" stroke-width="1.5"/>`;
  fg += `<text x="${X(n.lngLat[0]) + 8}" y="${Y(n.lngLat[1]) + 12}" font-size="12" font-weight="700" fill="${c}">${n.name}</text>`;
}
writeFileSync(outPath, `<!doctype html><meta charset="utf-8"><body style="margin:0;background:#f4f5f2;font-family:sans-serif"><svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">${bg}${fg}</svg></body>`);
