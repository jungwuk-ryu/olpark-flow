import type { Status, WalkResult } from '../../engine';

/**
 * 지도의 통행량 색 눈금.
 *
 * 색이 가리키는 숫자는 공연이나 조건이 달라져도 바뀌지 않는다. 그 화면에서 가장 큰 값에 맞춰 늘이거나 줄이지 않으므로
 * 같은 색은 언제나 같은 통행량이다. 눈금 값(stops)은 같은 간격의 색 단계에 놓이고 그 사이는 곧게 잇는다.
 * 마지막 눈금을 넘는 값은 마지막 색으로 칠한다.
 */
interface FlowScale {
  stops: readonly number[];
}

/**
 * 시간별 보기: 그 1분에 길 위를 지나던 사람 (엔진의 flow).
 * 평지 통과 용량이 폭 1m당 분당 82명이므로 폭 2.4m 길이 꽉 차면 200, 폭 4.9m 길이 꽉 차면 400이다.
 */
export const MINUTE_SCALE: FlowScale = { stops: [0, 10, 50, 100, 200, 400] };

/** 전체 시간대 보기: 저녁 내내 그 길을 지나간 사람 (엔진의 people) */
export const TOTAL_SCALE: FlowScale = { stops: [0, 100, 500, 1000, 2500, 5000] };

export const scaleFor = (minute: number | null): FlowScale => (minute === null ? TOTAL_SCALE : MINUTE_SCALE);

/**
 * 통행량이 적은 구간부터 회색에 가까운 연녹색, 초록, 노랑, 주황, 빨강 순서로 칠한다.
 * 혼잡도(보통·병목·위험)는 선 바깥의 별도 테두리로 표시한다.
 */
export const FLOW_RAMP = ['#c6d0c8', '#70a23e', '#b5ab2c', '#d8ad19', '#e47c28', '#ca4033'] as const;

/** 값이 색 눈금의 어디인지: 0(첫 눈금)~1(마지막 눈금 이상) */
export function scalePosition(value: number, stops: readonly number[]): number {
  const last = stops.length - 1;
  if (!(value > stops[0]!)) return 0;
  if (value >= stops[last]!) return 1;
  let i = 0;
  while (value >= stops[i + 1]!) i++;
  return (i + (value - stops[i]!) / (stops[i + 1]! - stops[i]!)) / last;
}

/** 범례 막대의 CSS 그러데이션 */
export const RAMP_CSS = `linear-gradient(to right, ${FLOW_RAMP.map((c, i) => `${c} ${(i / (FLOW_RAMP.length - 1)) * 100}%`).join(', ')})`;

export interface EdgeFlow {
  /** 색으로 칠하는 값: 시간별 보기는 명/분, 전체 시간대 보기는 명 */
  value: number;
  /** 테두리로 표시하는 혼잡도: 시간별 보기는 그 분, 전체 시간대 보기는 가장 나빴던 때 */
  status: Status;
}

/**
 * 지도에 칠할 길과 그 값. minute이 null이면 전체 시간대, 숫자면 종료 후 그 분(1부터)이다.
 * 시간별 보기에서는 그 분에 사람이 지나거나, 길 위에 서 있거나, 들어오려고 기다리는 길을 모두 칠한다.
 * 서 있기만 한 길은 통행량이 0이므로 첫 눈금의 연녹색이다.
 */
export function edgeFlows(result: WalkResult, minute: number | null): Map<string, EdgeFlow> {
  const out = new Map<string, EdgeFlow>();
  if (minute === null) {
    for (const s of Object.values(result.edges)) if (s.people >= 0.5) out.set(s.edgeId, { value: s.people, status: s.worst });
    return out;
  }
  const t = Math.min(result.minutes, minute) - 1;
  if (t < 0) return out;
  for (const s of Object.values(result.edges)) {
    const flow = s.flow[t]!;
    if (flow >= 0.5 || s.occupancy[t]! >= 0.5 || s.queue[t]! >= 0.5) out.set(s.edgeId, { value: flow, status: s.status[t]! });
  }
  return out;
}
