import type { EgressCurveInput } from './types';

/** 감마 함수 로그 (Lanczos 근사) */
function lnGamma(z: number): number {
  const g = 7;
  const c = [
    0.99999999999980993, 676.5203681218851, -1259.1392167224028, 771.32342877765313, -176.61502916214059,
    12.507343278686905, -0.13857109526572012, 9.9843695780195716e-6, 1.5056327351493116e-7,
  ];
  if (z < 0.5) return Math.log(Math.PI / Math.sin(Math.PI * z)) - lnGamma(1 - z);
  const x = z - 1;
  let a = c[0]!;
  const t = x + g + 0.5;
  for (let i = 1; i < g + 2; i++) a += c[i]! / (x + i);
  return 0.5 * Math.log(2 * Math.PI) + (x + 0.5) * Math.log(t) - t + Math.log(a);
}

/** 정규화된 하부 불완전 감마 함수 P(a, x) */
function regularizedLowerGamma(a: number, x: number): number {
  if (x <= 0) return 0;
  const gln = lnGamma(a);
  if (x < a + 1) {
    // 급수 전개
    let sum = 1 / a;
    let del = sum;
    let ap = a;
    for (let n = 0; n < 1000; n++) {
      ap += 1;
      del *= x / ap;
      sum += del;
      if (Math.abs(del) < Math.abs(sum) * 1e-14) break;
    }
    return sum * Math.exp(-x + a * Math.log(x) - gln);
  }
  // 연분수 전개 (Lentz)
  const tiny = 1e-300;
  let b = x + 1 - a;
  let c = 1 / tiny;
  let d = 1 / b;
  let h = d;
  for (let i = 1; i < 1000; i++) {
    const an = -i * (i - a);
    b += 2;
    d = an * d + b;
    if (Math.abs(d) < tiny) d = tiny;
    c = b + an / c;
    if (Math.abs(c) < tiny) c = tiny;
    d = 1 / d;
    const del = d * c;
    h *= del;
    if (Math.abs(del - 1) < 1e-14) break;
  }
  return 1 - Math.exp(-x + a * Math.log(x) - gln) * h;
}

/** 감마 분포 누적분포함수. shape = k, scale = θ */
export function gammaCdf(x: number, shape: number, scale: number): number {
  if (x <= 0) return 0;
  return regularizedLowerGamma(shape, x / scale);
}

/** 총 퇴장 시간에 누적 이 비율이 나오도록 맞춘다 */
export const EGRESS_TOTAL_QUANTILE = 0.95;

/**
 * 총 퇴장 시간(95%가 나오는 시각)과 정점 시각(최빈값)으로 감마 모수를 구한다.
 * 최빈값 = (k−1)θ 이므로 θ = 정점 / (k−1). k가 커질수록 분포가 좁아지므로 이분 탐색으로 k를 찾는다.
 */
export function gammaParamsFrom(totalMinutes: number, peakMinute: number): { shape: number; scale: number } {
  if (!(peakMinute > 0 && totalMinutes > peakMinute)) {
    throw new Error(`정점 시각(${peakMinute}분)은 0보다 크고 총 퇴장 시간(${totalMinutes}분)보다 작아야 합니다`);
  }
  const at = (k: number) => gammaCdf(totalMinutes, k, peakMinute / (k - 1));
  let lo = 1 + 1e-9;
  let hi = 2;
  while (at(hi) < EGRESS_TOTAL_QUANTILE && hi < 1e6) hi *= 2;
  for (let i = 0; i < 200; i++) {
    const mid = (lo + hi) / 2;
    if (at(mid) < EGRESS_TOTAL_QUANTILE) lo = mid;
    else hi = mid;
    if (hi - lo < 1e-10) break;
  }
  const shape = (lo + hi) / 2;
  return { shape, scale: peakMinute / (shape - 1) };
}

/** 분마다 공연장을 나오는 비율 (길이 = horizon, 합 = 1) */
export function egressFractions(curve: EgressCurveInput, horizon: number): number[] {
  let raw: number[];
  if (curve.kind === 'custom') {
    raw = Array.from({ length: horizon }, (_, t) => Math.max(0, curve.fractions[t] ?? 0));
  } else {
    const { shape, scale } = gammaParamsFrom(curve.totalMinutes, curve.peakMinute);
    raw = Array.from({ length: horizon }, (_, t) => gammaCdf(t + 1, shape, scale) - gammaCdf(t, shape, scale));
  }
  const sum = raw.reduce((a, b) => a + b, 0);
  if (sum <= 0) throw new Error('퇴장 곡선의 합이 0입니다');
  return raw.map((v) => v / sum);
}
