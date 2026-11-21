import { create } from 'zustand';
import type { DataBundle, EventItem, Prediction } from '../data/contract';
import { defaultParams, venueHasNetwork, type ScenarioParams, type ScenarioResult } from '../model/scenario';
import { computeScenarioAsync } from '../model/runner';

let requestSeq = 0;

type Status = 'loading' | 'computing' | 'ready' | 'dirty' | 'no-network' | 'error';

interface ScenarioState {
  data: DataBundle | null;
  eventId: string | null;
  params: ScenarioParams | null;
  /** 마지막으로 계산한 결과. 조건을 바꿔도 "다시 계산" 전까지는 그대로 둔다 */
  result: ScenarioResult | null;
  status: Status;
  error: string | null;
  /** 지도·차트에서 보고 있는 시점 (종료 후 분) */
  minute: number;

  init: (data: DataBundle) => void;
  selectEvent: (id: string) => void;
  setParam: <K extends keyof ScenarioParams>(key: K, value: ScenarioParams[K]) => void;
  recompute: () => void;
  setMinute: (m: number) => void;
}

export const selectEvent = (s: ScenarioState): EventItem | null => s.data?.events.events.find((e) => e.id === s.eventId) ?? null;
export const selectPrediction = (s: ScenarioState): Prediction | null =>
  s.data?.predictions.predictions.find((p) => p.eventId === s.eventId) ?? null;

export const useScenario = create<ScenarioState>((set, get) => {
  /** 계산은 Worker에서 돌리고, 끝나면 결과를 넣는다 */
  function run() {
    const s = get();
    const event = selectEvent(s);
    const prediction = selectPrediction(s);
    if (!s.data || !event || !prediction || !s.params) return;
    if (!venueHasNetwork(s.data, event.venueId)) {
      set({ status: 'no-network', result: null });
      return;
    }
    set({ status: 'computing' });
    const ticket = ++requestSeq;
    computeScenarioAsync(s.data, prediction, s.params).then(
      (result) => {
        // 계산하는 동안 다른 공연을 고르거나 다시 계산을 눌렀으면 이 결과는 버린다
        if (ticket !== requestSeq) return;
        const peak = result.current.totals.peakQueueTotalMinute;
        set({ result, status: 'ready', error: null, minute: Math.min(60, Math.max(0, peak)) });
      },
      (e: Error) => {
        if (ticket !== requestSeq) return;
        set({ status: 'error', error: e.message });
      },
    );
  }

  return {
    data: null,
    eventId: null,
    params: null,
    result: null,
    status: 'loading',
    error: null,
    minute: 0,

    init: (data) => {
      const first = data.events.events.find((e) => venueHasNetwork(data, e.venueId)) ?? data.events.events[0];
      set({ data });
      if (first) get().selectEvent(first.id);
    },

    selectEvent: (id) => {
      const s = get();
      const p = s.data?.predictions.predictions.find((x) => x.eventId === id);
      set({ eventId: id, params: p ? defaultParams(p) : null, result: null });
      run();
    },

    setParam: (key, value) => {
      const s = get();
      if (!s.params) return;
      set({ params: { ...s.params, [key]: value }, status: s.result ? 'dirty' : s.status });
    },

    recompute: run,

    setMinute: (m) => set({ minute: m }),
  };
});
