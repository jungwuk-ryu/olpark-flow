const nf = new Intl.NumberFormat('ko-KR');

/** 천 단위 쉼표. 소수 자릿수는 digits로 고정한다. */
export function formatNumber(value: number, digits = 0): string {
  if (!Number.isFinite(value)) return '–';
  const rounded = Number(value.toFixed(digits));
  return digits > 0
    ? rounded.toLocaleString('ko-KR', { minimumFractionDigits: digits, maximumFractionDigits: digits })
    : nf.format(Math.round(rounded));
}

/** 현재 대비 변화율. 감소면 음수. 기준이 0이면 null. */
export function changeRatio(current: number, next: number): number | null {
  if (current === 0) return null;
  return (next - current) / current;
}

/** 0.67 → "67%" */
export function formatPercent(ratio: number, digits = 0): string {
  return `${formatNumber(Math.abs(ratio) * 100, digits)}%`;
}

const WEEKDAY = ['일', '월', '화', '수', '목', '금', '토'];

/** 2026.11.21 (토) */
export function formatDateKo(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}.${p(d.getMonth() + 1)}.${p(d.getDate())} (${WEEKDAY[d.getDay()]})`;
}
