// geo/width/readings.json(항공영상 판독 기록)을 src/data/network.json과 geo/measurements.csv에 반영한다.
// 실행: node geo/width/apply-readings.mjs  (그 뒤 node geo/make-measurements.mjs 로 표 정리)
import { readFileSync, writeFileSync } from 'node:fs';
const netUrl = new URL('../../src/data/network.json', import.meta.url);
const csvUrl = new URL('../measurements.csv', import.meta.url);
const { readings } = JSON.parse(readFileSync(new URL('./readings.json', import.meta.url), 'utf8'));
const net = JSON.parse(readFileSync(netUrl, 'utf8'));
const METHOD = '브이월드 항공영상(봄)+Esri 항공영상(가을) 판독, 1m 눈금자';
const IMAGE_DATE = '브이월드 Satellite·Esri World Imagery (촬영 연도 미표기)';
const eff = (r) => Math.round((r.total - r.obst - r.shy) * 10) / 10;
for (const e of net.edges) {
  const r = readings[e.id];
  if (!r) throw new Error(`판독 기록 없음: ${e.id}`);
  e.widthM = eff(r);
  // 줄 설 공간: 토막 끝 40m 안쪽의 보행 가능 면적(광장은 상한 폭 기준)
  e.queueAreaM2 = Math.round(e.widthM * Math.min(40, Math.max(10, e.lengthM)));
  if (e.type === 'stairs') e.queueAreaM2 = 150;
  e.source = '가정';
}
writeFileSync(netUrl, JSON.stringify(net, null, 2) + '\n');

// 측정표: make-measurements.mjs가 만든 빈 칸을 채움
const lines = readFileSync(csvUrl, 'utf8').replace(/^﻿/, '').split(/\r?\n/).filter(Boolean);
const head = lines[0].split(',');
const col = (k) => head.indexOf(k);
const split = (line) => { const o = []; let c = '', q = false; for (let i = 0; i < line.length; i++) { const ch = line[i]; if (q) { if (ch === '"' && line[i + 1] === '"') { c += '"'; i++; } else if (ch === '"') q = false; else c += ch; } else if (ch === '"') q = true; else if (ch === ',') { o.push(c); c = ''; } else c += ch; } o.push(c); return o; };
const quote = (v) => (/[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v));
const out = [lines[0]];
for (const line of lines.slice(1)) {
  const v = split(line);
  const id = v[col('edgeId')];
  const r = readings[id];
  const e = net.edges.find((x) => x.id === id);
  if (r && e) {
    v[col('currentWidthM')] = e.widthM;
    v[col('widthTotalM')] = r.total;
    v[col('obstaclesM')] = r.obst;
    v[col('shyM')] = r.shy;
    v[col('widthEffectiveM')] = eff(r);
    v[col('areaM2')] = e.queueAreaM2;
    v[col('method')] = e.type === 'stairs' ? '항공영상 판독 불가 → 설계 기준값' : METHOD;
    v[col('imageDate')] = IMAGE_DATE;
    v[col('confidence')] = r.conf;
    v[col('memo')] = r.memo;
  }
  out.push(v.map(quote).join(','));
}
writeFileSync(csvUrl, '﻿' + out.join('\n') + '\n');
console.log('반영', Object.keys(readings).length, '개 토막');
