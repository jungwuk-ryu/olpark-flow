import type { Status } from './types';

/** 1인당 면적이 정체 기준의 이 배수 이하로 내려오면 "근접"으로 보고 위험 판정 */
export const DANGER_AREA_MARGIN = 1.2;

export const SATURATION_NORMAL = 0.7;
export const SATURATION_BOTTLENECK = 1.0;

/**
 * 상태 색 규칙
 * - 대기 중이고 1인당 면적이 정체 기준(0.46㎡)에 근접 → 위험
 * - 포화도 ≥ 1.0 → 병목, 0.7 ~ 1.0 → 보통, 0.7 미만 → 여유
 */
export function statusOf(p: {
  saturation: number;
  queue: number;
  areaPerPersonM2: number | null;
  jamAreaPerPersonM2: number;
}): Status {
  if (p.queue > 0 && p.areaPerPersonM2 !== null && p.areaPerPersonM2 <= p.jamAreaPerPersonM2 * DANGER_AREA_MARGIN) {
    return '위험';
  }
  if (p.saturation >= SATURATION_BOTTLENECK) return '병목';
  if (p.saturation >= SATURATION_NORMAL) return '보통';
  return '여유';
}
