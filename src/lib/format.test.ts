import { describe, expect, it } from 'vitest';
import { changeRatio, daysAhead, formatChange, formatClock, formatClockRange, formatDateKo, formatDay, formatEventEnd, formatMonths, formatNumber, formatPercent, koreanDate } from './format';

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

describe('formatEventEnd', () => {
  it('한국 시간 기준 "월.일 (요일) · 시:분"', () => {
    expect(formatEventEnd('2026-11-21T21:40:00+09:00')).toBe('11.21 (토) · 21:40');
  });
});

describe('formatChange', () => {
  it('감소·증가·변화 없음을 문장으로', () => {
    expect(formatChange(0.67)).toBe('67% 감소');
    expect(formatChange(-0.12)).toBe('12% 증가');
    expect(formatChange(0.001)).toBe('변화 없음');
    expect(formatChange(null)).toBe('비교 불가');
  });
});

describe('formatDateKo', () => {
  it('요일을 붙인다', () => {
    expect(formatDateKo(new Date(2026, 10, 21))).toBe('2026.11.21 (토)');
  });
});

describe('formatClock', () => {
  it('보는 사람의 시간대와 무관하게 한국 시간으로 적는다', () => {
    expect(formatClock('2026-11-27T21:08:00+09:00')).toBe('21:08');
    expect(formatClock('2026-11-27T12:08:00Z')).toBe('21:08');
    expect(formatClock(new Date('2026-11-27T00:05:00+09:00'))).toBe('00:05');
  });
  it('읽을 수 없는 시각은 대시로 표시한다', () => {
    expect(formatClock('모름')).toBe('–');
  });
  it('범위는 물결표로 잇고, 같은 분이면 하나만 적는다', () => {
    expect(formatClockRange('2026-11-27T20:45:00+09:00', '2026-11-27T21:31:00+09:00')).toBe('20:45~21:31');
    expect(formatClockRange('2026-11-27T21:08:00+09:00', '2026-11-27T21:08:30+09:00')).toBe('21:08');
  });
});

describe('날짜', () => {
  it('날짜를 요일과 함께 적는다', () => {
    expect(formatDay('2026-11-27')).toBe('11.27 (금)');
    expect(formatDay('2026-11-27T19:00:00+09:00')).toBe('11.27 (금)');
    // 한국 시간으로는 이미 다음 날인 시각
    expect(formatDay('2026-11-27T15:30:00Z')).toBe('11.28 (토)');
    expect(formatDay('모름')).toBe('–');
  });
  it('ISO 시각의 한국 날짜를 낸다', () => {
    expect(koreanDate('2026-11-27T23:59:00+09:00')).toBe('2026-11-27');
    expect(koreanDate('2026-11-27T15:00:00Z')).toBe('2026-11-28');
  });
  it('기간은 달까지만 적는다', () => {
    expect(formatMonths('2025-04-05', '2026-11-15')).toBe('2025.04~2026.11');
    expect(formatMonths('2026-11-01', '2026-11-15')).toBe('2026.11');
  });
  it('며칠 뒤인지 말로 옮긴다', () => {
    expect(daysAhead('2026-11-26', '2026-11-26')).toBe('오늘');
    expect(daysAhead('2026-11-27', '2026-11-26')).toBe('내일');
    expect(daysAhead('2026-12-04', '2026-11-26')).toBe('D-8');
    expect(daysAhead('2026-11-25', '2026-11-26')).toBe('지남');
  });
});
