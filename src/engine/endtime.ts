/**
 * 종료 시각의 불확실성.
 *
 * 시뮬레이션은 "종료 후 t분"으로 계산한다. 공연이 예상보다 d분 늦게 끝나면 같은 흐름이 d분 늦게 나타난다.
 * 종료 시각은 전날에 확정되지 않으므로, 시계 시각으로 본 결과는 한 줄이 아니라 범위다.
 * 이 파일은 종료 후 분 기준 계열을 시계 시각 기준 범위로 옮긴다.
 * 크기(대기 인원 최댓값, 대기시간 합계)는 종료 시각이 움직여도 바뀌지 않는다.
 */

/** 예상 종료 시각 대비 실제 종료가 움직이는 범위 (분). earlyMinutes ≤ 0 ≤ lateMinutes */
export interface EndWindow {
  earlyMinutes: number;
  lateMinutes: number;
}

/** 종료 시각을 확정값으로 볼 때 */
export const FIXED_END: EndWindow = { earlyMinutes: 0, lateMinutes: 0 };

const MINUTE_MS = 60_000;
const ms = (t: string | number | Date) => (t instanceof Date ? t.getTime() : typeof t === 'number' ? t : Date.parse(t));

/** 예상·가장 이른·가장 늦은 종료 시각으로 범위를 만든다. 분 단위로 바깥쪽으로 올림한다 */
export function endWindowFrom(expectedAt: string | number | Date, earliestAt: string | number | Date, latestAt: string | number | Date): EndWindow {
  const expected = ms(expectedAt);
  const early = Math.floor((ms(earliestAt) - expected) / MINUTE_MS);
  const late = Math.ceil((ms(latestAt) - expected) / MINUTE_MS);
  if (![expected, early, late].every(Number.isFinite)) throw new Error('종료 시각을 읽을 수 없습니다');
  return { earlyMinutes: Math.min(0, early), lateMinutes: Math.max(0, late) };
}

/**
 * 종료 후 x분 시점의 값. 계열의 t번째 값은 종료 후 t+1분 시점의 값이다.
 * 종료 전(x ≤ 0)은 0, 계산 범위를 넘으면 마지막 값(아직 풀리지 않은 줄은 남아 있다고 본다).
 */
export function valueAfterEnd(series: readonly number[], x: number): number {
  if (x <= 0 || series.length === 0) return 0;
  return series[Math.min(series.length, Math.ceil(x)) - 1]!;
}

export interface ClockBand {
  /** 예상 종료 시각 기준 분 (음수 = 예상 종료 전) */
  x: number;
  low: number;
  high: number;
}

/**
 * 종료 시각이 window 안에서 움직일 때, 시계 시각 x(예상 종료 시각 기준 분)에 값이 닿는 범위.
 * 실제 종료가 예상보다 d분 늦으면 x의 값은 series(x − d)이므로 d ∈ [early, late]에 대한 최소·최대를 낸다.
 */
export function clockEnvelope(series: readonly number[], window: EndWindow, from: number, to: number): ClockBand[] {
  const early = Math.floor(Math.min(0, window.earlyMinutes));
  const late = Math.ceil(Math.max(0, window.lateMinutes));
  const out: ClockBand[] = [];
  for (let x = Math.floor(from); x <= Math.ceil(to); x++) {
    let low = Infinity;
    let high = -Infinity;
    for (let d = early; d <= late; d++) {
      const v = valueAfterEnd(series, x - d);
      if (v < low) low = v;
      if (v > high) high = v;
    }
    out.push({ x, low, high });
  }
  return out;
}

export interface ClockTimes {
  /** 예상 종료 시각에 끝났을 때의 시각 */
  at: Date;
  /** 종료 시각 범위의 이른 끝에서 끝났을 때 */
  earliest: Date;
  /** 종료 시각 범위의 늦은 끝에서 끝났을 때 */
  latest: Date;
}

/** 종료 후 minute분에 일어나는 일의 시계 시각과 그 범위 */
export function clockTimes(expectedEndAt: string | number | Date, minuteAfterEnd: number, window: EndWindow = FIXED_END): ClockTimes {
  const base = ms(expectedEndAt) + minuteAfterEnd * MINUTE_MS;
  if (!Number.isFinite(base)) throw new Error('종료 시각을 읽을 수 없습니다');
  return {
    at: new Date(base),
    earliest: new Date(base + Math.min(0, window.earlyMinutes) * MINUTE_MS),
    latest: new Date(base + Math.max(0, window.lateMinutes) * MINUTE_MS),
  };
}
