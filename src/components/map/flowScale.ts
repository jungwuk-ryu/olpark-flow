import type { Status, WalkResult } from '../../engine';
import { SATURATION_NORMAL } from '../../engine/status';

/** 상태색 (PRODUCT.md: 여유 #3E8E6E / 보통 #D9B44A / 병목 #E07B39 / 위험 #C8423B) */
export const STATUS_HEX: Record<Status, string> = { 여유: '#3e8e6e', 보통: '#d9b44a', 병목: '#e07b39', 위험: '#c8423b' };

/**
 * 지도의 혼잡도 색 눈금 (0~1).
 *
 * 선 색은 엔진이 판정한 그 길의 상태다. 여유인 길만 포화도(들어오려는 사람 ÷ 통과 용량)에 따라 옅은 색에서 초록으로 진해지고,
 * 보통·병목·위험은 각자의 상태색 한 가지로 칠한다. 사람이 꽉 차 움직이지 못하는 길은 통행량이 0이어도 위험 색이다.
 * 눈금 사이의 색은 재생 중 1분에서 다음 1분으로 넘어갈 때만 잠깐 보인다.
 */
export const CONGESTION_RAMP = [
  // 사람이 지나는 길은 여유라도 배경 지도와 구별되게 옅은 초록에서 시작한다
  { at: 0, color: '#7db597' },
  { at: 0.5, color: STATUS_HEX.여유 },
  { at: 2 / 3, color: STATUS_HEX.보통 },
  { at: 5 / 6, color: STATUS_HEX.병목 },
  { at: 1, color: STATUS_HEX.위험 },
] as const;

const STATUS_AT: Record<Exclude<Status, '여유'>, number> = { 보통: 2 / 3, 병목: 5 / 6, 위험: 1 };

/** 상태와 포화도가 색 눈금의 어디인지 (0~1). 여유 안에서는 보통 기준(포화도 0.7)에 가까울수록 진한 초록이다 */
export function congestionPosition(status: Status, saturation: number): number {
  if (status !== '여유') return STATUS_AT[status];
  if (!(saturation > 0)) return 0;
  return 0.5 * Math.min(1, saturation / SATURATION_NORMAL);
}

/**
 * 선 굵기 (px). 지도 배율에 따라 굵어지고, 병목·위험인 길은 짧은 토막도 멀리서 보이도록 더 굵게 그린다.
 * 배율 13에서 보통은 3px, 병목·위험은 7px이다.
 */
const WIDTH_BY_ZOOM = [
  { zoom: 13, normal: 3, wide: 7 },
  { zoom: 15, normal: 4.5, wide: 9 },
  { zoom: 17, normal: 6, wide: 11 },
  { zoom: 19, normal: 8, wide: 13 },
] as const;

/** 이 눈금 위치(병목) 이상이면 굵게 그린다 */
const WIDE_FROM = STATUS_AT.병목;

/** 색 눈금 위치와 지도 배율(표의 배율 중 하나)에서의 선 굵기 */
export function lineWidth(level: number, zoom: number): number {
  const row = WIDTH_BY_ZOOM.find((x) => x.zoom === zoom);
  if (!row) throw new Error(`선 굵기 표에 없는 배율: ${zoom}`);
  return level >= WIDE_FROM ? row.wide : row.normal;
}

/** 지도 층의 line-width 표현식. extra는 선 바깥으로 두르는 테두리 두께 */
export function lineWidthExpression(extra = 0): number {
  const level = ['coalesce', ['feature-state', 'v'], 0];
  return [
    'interpolate',
    ['linear'],
    ['zoom'],
    ...WIDTH_BY_ZOOM.flatMap((x) => [x.zoom, ['case', ['>=', level, WIDE_FROM], lineWidth(WIDE_FROM, x.zoom) + extra, lineWidth(0, x.zoom) + extra]]),
  ] as unknown as number;
}

/** 가장 굵은 선(병목·위험)의 너비에 extra를 더한 표현식. 선 양옆에 두르는 표시가 어떤 상태의 선과도 겹치지 않게 한다 */
export function outerWidthExpression(extra = 0): number {
  return ['interpolate', ['linear'], ['zoom'], ...WIDTH_BY_ZOOM.flatMap((x) => [x.zoom, lineWidth(WIDE_FROM, x.zoom) + extra])] as unknown as number;
}

/** 범례 막대의 CSS 그러데이션 */
export const RAMP_CSS = `linear-gradient(to right, ${CONGESTION_RAMP.map((x) => `${x.color} ${x.at * 100}%`).join(', ')})`;

export interface EdgeFlow {
  /** 통행량: 시간별 보기는 명/분, 전체 시간대 보기는 명 */
  value: number;
  /** 혼잡도: 시간별 보기는 그 분, 전체 시간대 보기는 가장 나빴던 때 */
  status: Status;
  /** 포화도: 시간별 보기는 그 분, 전체 시간대 보기는 가장 높았던 때 */
  saturation: number;
  /** 색 눈금 위치 (congestionPosition) */
  level: number;
}

const flowOf = (value: number, status: Status, saturation: number): EdgeFlow => ({ value, status, saturation, level: congestionPosition(status, saturation) });

/**
 * 지도에 칠할 길과 그 값. minute이 null이면 전체 시간대, 숫자면 종료 후 그 분(1부터)이다.
 * 시간별 보기에서는 그 분에 사람이 지나거나, 길 위에 서 있거나, 들어오려고 기다리는 길을 모두 칠한다.
 * 서 있기만 한 길도 통행량과 관계없이 그 분의 혼잡도로 칠한다.
 */
export function edgeFlows(result: WalkResult, minute: number | null): Map<string, EdgeFlow> {
  const out = new Map<string, EdgeFlow>();
  if (minute === null) {
    for (const s of Object.values(result.edges)) if (s.people >= 0.5) out.set(s.edgeId, flowOf(s.people, s.worst, Math.max(...s.saturation)));
    return out;
  }
  const t = Math.min(result.minutes, minute) - 1;
  if (t < 0) return out;
  for (const s of Object.values(result.edges)) {
    const flow = s.flow[t]!;
    if (flow >= 0.5 || s.occupancy[t]! >= 0.5 || s.queue[t]! >= 0.5) out.set(s.edgeId, flowOf(flow, s.status[t]!, s.saturation[t]!));
  }
  return out;
}
