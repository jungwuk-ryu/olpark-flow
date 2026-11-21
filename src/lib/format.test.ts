import { describe, expect, it } from 'vitest';
import { changeRatio, formatDateKo, formatNumber, formatPercent } from './format';

describe('formatNumber', () => {
  it('천 단위 쉼표를 붙인다', () => {
    expect(formatNumber(27837)).toBe('27,837');
    expect(formatNumber(1847.6)).toBe('1,848');
  });
  it('소수 자릿수를 고정한다', () => {
    expect(formatNumber(3.54, 1)).toBe('3.5');
    expect(formatNumber(3, 1)).toBe('3.0');
  });
  it('유한하지 않은 값은 대시로 표시한다', () => {
    expect(formatNumber(Number.NaN)).toBe('–');
  });
});

describe('changeRatio / formatPercent', () => {
  it('감소율을 계산한다', () => {
    const r = changeRatio(1847, 615);
    expect(r).not.toBeNull();
    expect(formatPercent(r!)).toBe('67%');
  });
  it('기준이 0이면 null', () => {
    expect(changeRatio(0, 10)).toBeNull();
  });
});

describe('formatDateKo', () => {
  it('요일을 붙인다', () => {
    expect(formatDateKo(new Date(2026, 10, 21))).toBe('2026.11.21 (토)');
  });
});
