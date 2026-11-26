import { create } from 'zustand';
import { NO_CONTROLS, type LngLat, type WalkControls } from '../engine';
import { blockEdge, narrowEdge, removeControl, staffEdge } from '../model/walkplan';

/** 되돌리기로 돌아갈 수 있는 단계 수 */
const UNDO_LIMIT = 30;

interface WalkState {
  /** 운영자가 지도에 놓은 것. 안내를 따르는 비율은 계산할 때 화면의 조건에서 가져온다 */
  controls: WalkControls;
  /** 놓기 전의 모습들. 가장 최근 것이 끝에 있다 */
  history: WalkControls[];
  /** null이면 저녁 전체에서 가장 붐빈 정도를, 숫자면 종료 후 그 분의 모습을 본다 */
  minute: number | null;
  playing: boolean;
  /** 지도에서 고른 길과 선택 상자를 띄울 자리 */
  selected: string | null;
  pickAt: LngLat | null;
  /** 지도가 옮겨 가 보여 줄 길. 같은 길을 다시 골라도 옮겨 가도록 번호를 붙인다 */
  focus: { edgeId: string; turn: number } | null;

  setMinute: (minute: number | null) => void;
  setPlaying: (playing: boolean) => void;
  /** 길을 고른다. at은 선택 상자를 띄울 자리 (누른 곳) */
  select: (edgeId: string | null, at?: LngLat) => void;
  /** 목록에서 길을 골라 지도를 그 길로 옮긴다 */
  focusOn: (edgeId: string, at: LngLat) => void;
  block: (edgeId: string) => void;
  narrow: (edgeId: string, widthM: number) => void;
  staff: (edgeId: string, sendTo?: string) => void;
  remove: (edgeId: string) => void;
  clear: () => void;
  undo: () => void;
  /** 다른 공연을 고르면 놓은 것과 보던 시점을 처음으로 돌린다 */
  reset: () => void;
}

export const useWalk = create<WalkState>((set, get) => {
  /**
   * 놓은 것을 바꾸고, 바꾸기 전 모습을 되돌리기 목록에 쌓는다.
   * adjust면 이미 놓은 것의 값(남은 폭, 안내할 역)만 고치는 것이라 쌓지 않는다. 막대를 끄는 동안 단계가 수십 개 생기지 않게 한다
   */
  const change = (next: (c: WalkControls) => WalkControls, adjust = false) => {
    const { controls, history } = get();
    const after = next(controls);
    if (after === controls) return;
    set(adjust ? { controls: after } : { controls: after, history: [...history, controls].slice(-UNDO_LIMIT) });
  };
  return {
    controls: NO_CONTROLS,
    history: [],
    minute: null,
    playing: false,
    selected: null,
    pickAt: null,
    focus: null,

    setMinute: (minute) => set({ minute }),
    setPlaying: (playing) => set((s) => ({ playing, minute: playing && s.minute === null ? 0 : s.minute })),
    select: (edgeId, at) => set({ selected: edgeId, pickAt: edgeId ? (at ?? null) : null }),
    focusOn: (edgeId, at) => set((s) => ({ selected: edgeId, pickAt: at, focus: { edgeId, turn: (s.focus?.turn ?? 0) + 1 } })),
    block: (edgeId) => change((c) => blockEdge(c, edgeId)),
    narrow: (edgeId, widthM) => change((c) => narrowEdge(c, edgeId, widthM), get().controls.narrowed[edgeId] !== undefined),
    staff: (edgeId, sendTo) => change((c) => staffEdge(c, edgeId, sendTo), get().controls.staffed.some((s) => s.edgeId === edgeId)),
    remove: (edgeId) => change((c) => removeControl(c, edgeId)),
    clear: () => change((c) => (c.blocked.length || c.staffed.length || Object.keys(c.narrowed).length ? NO_CONTROLS : c)),
    undo: () => {
      const { history } = get();
      if (!history.length) return;
      set({ controls: history[history.length - 1]!, history: history.slice(0, -1) });
    },
    reset: () => set({ controls: NO_CONTROLS, history: [], minute: null, playing: false, selected: null, pickAt: null, focus: null }),
  };
});
