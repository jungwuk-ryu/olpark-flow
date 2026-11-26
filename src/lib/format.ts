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

const clock = new Intl.DateTimeFormat('ko-KR', { timeZone: 'Asia/Seoul', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });

/** 시각을 한국 시간 "21:08"로 */
export function formatClock(t: string | number | Date): string {
  const d = t instanceof Date ? t : new Date(t);
  if (Number.isNaN(d.getTime())) return '–';
  const parts = clock.formatToParts(d);
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? '';
  return `${get('hour')}:${get('minute')}`;
}

/** 두 시각을 "20:45~21:31"로. 같은 분이면 하나만 */
export function formatClockRange(from: string | number | Date, to: string | number | Date): string {
  const a = formatClock(from);
  const b = formatClock(to);
  return a === b ? a : `${a}~${b}`;
}

const WEEKDAY = ['일', '월', '화', '수', '목', '금', '토'];
const DAY_MS = 86_400_000;

/** 날짜(YYYY-MM-DD 또는 ISO 시각의 한국 날짜)를 "11.27 (금)"으로 */
export function formatDay(t: string): string {
  const iso = t.length === 10 ? t : koreanDate(t);
  const [y, m, d] = iso.split('-').map(Number);
  if (!y || !m || !d) return '–';
  const weekday = WEEKDAY[new Date(Date.UTC(y, m - 1, d)).getUTCDay()];
  return `${String(m).padStart(2, '0')}.${String(d).padStart(2, '0')} (${weekday})`;
}

/** 날짜 두 개를 달까지만 "2025.04~2026.11"로. 같은 달이면 하나만 */
export function formatMonths(first: string, last: string): string {
  const month = (d: string) => d.slice(0, 7).replace('-', '.');
  return month(first) === month(last) ? month(first) : `${month(first)}~${month(last)}`;
}

/** ISO 시각이 한국 시간으로 며칠인지 (YYYY-MM-DD) */
export function koreanDate(t: string | number | Date): string {
  const ms = t instanceof Date ? t.getTime() : typeof t === 'number' ? t : Date.parse(t);
  return Number.isFinite(ms) ? new Date(ms + 9 * 3_600_000).toISOString().slice(0, 10) : '';
}

/** 기준 날짜에서 며칠 뒤인지: "오늘", "내일", "D-8". 지난 날짜는 "지남" */
export function daysAhead(date: string, today: string): string {
  const diff = Math.round((Date.parse(`${date}T00:00:00Z`) - Date.parse(`${today}T00:00:00Z`)) / DAY_MS);
  if (!Number.isFinite(diff)) return '';
  return diff === 0 ? '오늘' : diff === 1 ? '내일' : diff > 1 ? `D-${diff}` : '지남';
}

/** 2026.11.21 (토) */
export function formatDateKo(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}.${p(d.getMonth() + 1)}.${p(d.getDate())} (${WEEKDAY[d.getDay()]})`;
}
