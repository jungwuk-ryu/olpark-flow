import type { LngLat, WalkResult } from '../../engine';

/** 이 배율보다 축소했을 때만 핀을 띄운다. 그보다 크게 보면 굵은 선으로 충분히 보인다 */
export const PINS_BELOW_ZOOM = 15.5;
/** 이 거리(m) 안의 위험 구간은 핀 하나로 묶는다 */
export const PIN_MERGE_M = 30;
/** 한 화면에 띄우는 핀 수 */
export const PIN_MAX = 5;
/** 통제·장애물·안전요원 표시가 이 거리(m) 안에 있으면 핀을 위로 띄워 겹치지 않게 한다 */
const LIFT_WITHIN_M = 20;

export const showPins = (zoom: number) => zoom < PINS_BELOW_ZOOM;

export interface WarningPin {
  /** 묶인 길. 첫째가 가장 붐빈 길이고 핀은 그 길 가운데에 놓인다 */
  edgeIds: string[];
  at: LngLat;
  count: number;
  /** 묶인 길 위에 선 사람의 합 (시간별 보기는 그 분, 전체 시간대 보기는 길마다 가장 많았던 때) */
  standing: number;
  /** 근처의 통제·배치 표시와 겹치지 않게 위로 띄울지 */
  lift: boolean;
}

/** 두 점 사이의 거리 (m). 공원 안의 짧은 거리라 평면으로 본다 */
function metres(a: LngLat, b: LngLat): number {
  const k = 111_320;
  const dx = (a[0] - b[0]) * k * Math.cos((((a[1] + b[1]) / 2) * Math.PI) / 180);
  const dy = (a[1] - b[1]) * k;
  return Math.hypot(dx, dy);
}

/**
 * 공원 전체를 볼 때 띄울 위험 경고 핀. minute이 null이면 전체 시간대, 숫자면 종료 후 그 분(1부터)이다.
 * 위험한 길만 고르고(병목은 굵은 선으로 본다), 길 위에 선 사람이 많은 순으로 가까운 곳끼리 묶어 PIN_MAX개까지 놓는다.
 * 판정은 엔진의 상태를 그대로 쓴다.
 */
export function warningPins(result: WalkResult, minute: number | null, mid: ReadonlyMap<string, LngLat>, markedAt: readonly LngLat[]): WarningPin[] {
  const t = minute === null ? null : Math.min(result.minutes, minute) - 1;
  if (t !== null && t < 0) return [];
  const danger: { id: string; at: LngLat; standing: number }[] = [];
  for (const s of Object.values(result.edges)) {
    const at = mid.get(s.edgeId);
    if (!at) continue;
    if (t === null ? s.worst !== '위험' : s.status[t] !== '위험') continue;
    danger.push({ id: s.edgeId, at, standing: t === null ? Math.max(...s.occupancy) : s.occupancy[t]! });
  }
  danger.sort((a, b) => b.standing - a.standing);

  const pins: WarningPin[] = [];
  for (const d of danger) {
    const home = pins.find((p) => metres(p.at, d.at) <= PIN_MERGE_M);
    if (home) {
      home.edgeIds.push(d.id);
      home.count++;
      home.standing += d.standing;
    } else {
      pins.push({ edgeIds: [d.id], at: d.at, count: 1, standing: d.standing, lift: markedAt.some((m) => metres(m, d.at) <= LIFT_WITHIN_M) });
    }
  }
  return pins.slice(0, PIN_MAX);
}
