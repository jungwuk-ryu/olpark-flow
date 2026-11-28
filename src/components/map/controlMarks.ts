import type { ControlKind } from '../../model/walkplan';

/**
 * 지도에 놓은 것(통제·장애물·안전요원)의 표시. 혼잡도 색(flowScale.ts)과 겹치지 않는 색만 쓴다.
 * - 통제: 길 위에 짙은 짧은 점선 (막힌 길에는 지나는 사람이 없어 혼잡도 선이 없다)
 * - 장애물: 혼잡도 선을 덮지 않고 양옆에 회색 긴 점선을 둘러 폭이 좁아졌음을 보인다
 * - 안전요원: 길 위에 브랜드 초록 실선
 */
export const CONTROL_LINE: Record<ControlKind, { color: string; width: number; dash?: [number, number]; besideLine: boolean }> = {
  block: { color: '#1f2a27', width: 5, dash: [0.6, 0.9], besideLine: false },
  narrow: { color: '#6b7570', width: 2, dash: [2, 1.2], besideLine: true },
  staff: { color: '#2c6b5f', width: 5, besideLine: false },
};

/** 길 가운데 아이콘의 바탕색 (Tailwind 색 토큰) */
export const CONTROL_CHIP_TONE: Record<ControlKind, string> = {
  block: 'bg-ink text-white',
  narrow: 'bg-ink-2 text-white',
  staff: 'bg-accent text-white',
};
