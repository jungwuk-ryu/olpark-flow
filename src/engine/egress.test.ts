import { describe, expect, it } from 'vitest';
import { egressFractions, gammaCdf, gammaParamsFrom } from './egress';

describe('gammaCdf', () => {
  it('형상 1이면 지수분포와 같다', () => {
    // k=1, θ=2 → CDF(x) = 1 - e^(-x/2)
    expect(gammaCdf(2, 1, 2)).toBeCloseTo(1 - Math.exp(-1), 6);
  });
  it('형상 2, 척도 1에서 알려진 값과 같다', () => {
    // P(2, 3) = 1 - e^-3 (1 + 3)
    expect(gammaCdf(3, 2, 1)).toBeCloseTo(1 - Math.exp(-3) * 4, 6);
  });
  it('0 이하에서는 0', () => {
    expect(gammaCdf(0, 3, 2)).toBe(0);
  });
});

describe('gammaParamsFrom', () => {
  it('정점 시각이 분포의 최빈값이 된다', () => {
    const { shape, scale } = gammaParamsFrom(22, 6);
    expect((shape - 1) * scale).toBeCloseTo(6, 6);
  });
  it('총 퇴장 시간에 95%가 나온다', () => {
    const { shape, scale } = gammaParamsFrom(22, 6);
    expect(gammaCdf(22, shape, scale)).toBeCloseTo(0.95, 3);
  });
  it('정점이 총 시간보다 늦으면 오류', () => {
    expect(() => gammaParamsFrom(10, 12)).toThrow();
  });
});

describe('egressFractions', () => {
  it('분 단위 비율의 합이 1이다', () => {
    const f = egressFractions({ kind: 'gamma', totalMinutes: 22, peakMinute: 6 }, 90);
    expect(f).toHaveLength(90);
    expect(f.reduce((a, b) => a + b, 0)).toBeCloseTo(1, 10);
  });
  it('가장 많이 나오는 분이 정점 시각 근처다', () => {
    const f = egressFractions({ kind: 'gamma', totalMinutes: 22, peakMinute: 6 }, 90);
    const argmax = f.indexOf(Math.max(...f));
    expect(Math.abs(argmax - 6)).toBeLessThanOrEqual(1);
  });
  it('직접 준 비율은 계산 범위에 맞춰 정규화한다', () => {
    const f = egressFractions({ kind: 'custom', fractions: [1, 1, 2] }, 5);
    expect(f).toEqual([0.25, 0.25, 0.5, 0, 0]);
  });
});
