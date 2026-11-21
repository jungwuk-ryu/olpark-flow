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

/** 0.67 → "67% 감소", -0.12 → "12% 증가". 0.5% 미만은 변화 없음 */
export function formatChange(reduction: number | null): string {
  if (reduction === null || !Number.isFinite(reduction)) return '비교 불가';
  if (Math.abs(reduction) < 0.005) return '변화 없음';
  return `${formatPercent(reduction)} ${reduction > 0 ? '감소' : '증가'}`;
}

/** ISO 시각을 한국 시간 "11.21 (토) · 21:40"으로 */
export function formatEventEnd(iso: string): string {
  const parts = new Intl.DateTimeFormat('ko-KR', {
    timeZone: 'Asia/Seoul',
    month: '2-digit',
    day: '2-digit',
    weekday: 'short',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(new Date(iso));
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? '';
  return `${get('month')}.${get('day')} (${get('weekday')}) · ${get('hour')}:${get('minute')}`;
}

const WEEKDAY = ['일', '월', '화', '수', '목', '금', '토'];

/** 2026.11.21 (토) */
export function formatDateKo(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}.${p(d.getMonth() + 1)}.${p(d.getDate())} (${WEEKDAY[d.getDay()]})`;
}
