/**
 * 계산의 역 도착 시간 폭을 실제 지하철 승차 집중도와 비교 (팀원 B 검증 ①-2).
 * 실행: pnpm vitest run -c geo/validate/vitest.config.ts
 */
import { describe, it } from 'vitest';
import { bundle } from '../../src/data/index';
import { simulate } from '../../src/engine/simulate';

const p = bundle.predictions.predictions.find((x) => x.eventId === 'kspo-20261121-a')!;
describe('역 도착 시간 폭', () => {
  for (const [tm, pk] of [[p.egressCurve!.totalMinutes, p.egressCurve!.peakMinute], [40, 10], [60, 15], [80, 20]] as const)
  it(`퇴장 ${tm}분(정점 ${pk}분)`, () => {
    const r = simulate({
      network: bundle.network,
      routes: bundle.routes,
      assumptions: bundle.assumptions,
      attendance: p.attendance!.value,
      egress: { kind: 'gamma', totalMinutes: tm, peakMinute: pk },
      directionShare: p.directionShare!,
      horizonMinutes: 180,
    });
    const a = r.ledger.arrived;
    const total = a[a.length - 1]!;
    const at = (q: number) => a.findIndex((v) => v >= total * q);
    let best60 = 0;
    for (let t = 0; t + 60 < a.length; t++) best60 = Math.max(best60, (a[t + 60]! - (t ? a[t - 1]! : 0)) / total);
    // 실제 데이터는 '시 단위' 합계라, 종료 시각이 시 사이 어디든 올 수 있다고 보고(0~59분 균등) 같은 방식으로 묶어 비교
    const perMin = a.map((v, i) => (i ? v - a[i - 1]! : v));
    let one = 0;
    let two = 0;
    for (let off = 0; off < 60; off++) {
      const bins: number[] = [];
      perMin.forEach((v, i) => { const b = Math.floor((i + off) / 60); bins[b] = (bins[b] ?? 0) + v; });
      const s = bins.filter((x) => x !== undefined).sort((x, y) => y - x);
      one += s[0]! / total / 60;
      two += ((s[0] ?? 0) + (s[1] ?? 0)) / total / 60;
    }
    console.log(`[퇴장 ${tm}분] 시 단위로 묶으면: 가장 붐빈 1시간 ${(one * 100).toFixed(0)}%, 상위 2시간 ${(two * 100).toFixed(0)}% (실제 2025 공연일 97일: 75%, 95%)`);
    console.log(`도착 50%: ${at(0.5)}분, 90%: ${at(0.9)}분, 99%: ${at(0.99)}분, 가장 붐빈 60분 비율 ${(best60 * 100).toFixed(0)}%`);
  });
});
