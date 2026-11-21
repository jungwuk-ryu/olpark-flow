// src/data/network.json으로 폭 측정 기록표(geo/measurements.csv)의 빈 칸을 만든다.
// 이미 적어 둔 측정값은 edgeId 기준으로 보존한다.
// 실행: node geo/make-measurements.mjs
import { existsSync, readFileSync, writeFileSync } from 'node:fs';

const net = JSON.parse(readFileSync(new URL('../src/data/network.json', import.meta.url), 'utf8'));
const out = new URL('./measurements.csv', import.meta.url);
const COLS = ['edgeId', 'from', 'to', 'lengthM', 'type', 'currentWidthM', 'widthTotalM', 'obstaclesM', 'shyM', 'widthEffectiveM', 'areaM2', 'method', 'imageDate', 'confidence', 'checkLat', 'checkLng', 'kakaoMap', 'memo'];
const KEEP = ['widthTotalM', 'obstaclesM', 'shyM', 'widthEffectiveM', 'areaM2', 'method', 'imageDate', 'confidence', 'memo'];

/** 따옴표를 지키는 CSV 한 줄 나누기 */
function splitCsv(line) {
  const out = [];
  let cur = '';
  let q = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (q) {
      if (ch === '"' && line[i + 1] === '"') { cur += '"'; i++; }
      else if (ch === '"') q = false;
      else cur += ch;
    } else if (ch === '"') q = true;
    else if (ch === ',') { out.push(cur); cur = ''; }
    else cur += ch;
  }
  out.push(cur);
  return out;
}
const quote = (v) => (/[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v);

const old = new Map();
if (existsSync(out)) {
  const [head, ...rows] = readFileSync(out, 'utf8').replace(/^﻿/, '').split(/\r?\n/).filter(Boolean);
  const h = splitCsv(head);
  for (const r of rows) {
    const v = splitCsv(r);
    old.set(v[h.indexOf('edgeId')], Object.fromEntries(h.map((k, i) => [k, v[i] ?? ''])));
  }
}
const nodeName = new Map(net.nodes.map((n) => [n.id, n.name]));
const lines = [COLS.join(',')];
for (const e of net.edges) {
  const mid = e.path ? e.path[Math.floor(e.path.length / 2)] : net.nodes.find((n) => n.id === e.from).lngLat;
  const prev = old.get(e.id) ?? {};
  const row = {
    edgeId: e.id,
    from: nodeName.get(e.from),
    to: nodeName.get(e.to),
    lengthM: e.lengthM,
    type: e.type,
    currentWidthM: e.widthM,
    checkLat: mid[1],
    checkLng: mid[0],
    kakaoMap: `https://map.kakao.com/link/map/${mid[1]},${mid[0]}`,
    ...Object.fromEntries(KEEP.map((k) => [k, prev[k] ?? ''])),
  };
  lines.push(COLS.map((c) => quote(String(row[c] ?? ''))).join(','));
}
writeFileSync(out, '﻿' + lines.join('\n') + '\n');
console.log(`${net.edges.length}개 길 토막 → geo/measurements.csv`);
