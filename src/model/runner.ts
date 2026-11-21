import { computeScenario, type ScenarioParams, type ScenarioResult } from './scenario';
import type { DataBundle, Prediction } from '../data/contract';

/**
 * 분산안 탐색(약 1초)을 화면과 다른 스레드에서 돌린다.
 * Worker가 없는 환경(테스트 등)에서는 다음 틱에 같은 스레드에서 계산한다.
 */
let worker: Worker | null = null;
let seq = 0;
const waiting = new Map<number, { resolve: (r: ScenarioResult) => void; reject: (e: Error) => void }>();

let workerBroken = false;
const pending = new Map<number, { data: DataBundle; prediction: Prediction; params: ScenarioParams }>();

function getWorker(): Worker | null {
  if (typeof Worker === 'undefined' || workerBroken) return null;
  if (!worker) {
    worker = new Worker(new URL('./scenario.worker.ts', import.meta.url), { type: 'module' });
    worker.onmessage = (e: MessageEvent<{ id: number; ok: boolean; result?: ScenarioResult; error?: string }>) => {
      const w = waiting.get(e.data.id);
      if (!w) return;
      waiting.delete(e.data.id);
      pending.delete(e.data.id);
      if (e.data.ok) w.resolve(e.data.result!);
      else w.reject(new Error(e.data.error));
    };
    // Worker를 못 띄우면 기다리던 계산을 화면 쪽에서 대신 한다
    worker.onerror = (ev) => {
      console.warn('[olpark] 계산 Worker 오류, 화면 스레드에서 계산합니다', ev.message);
      workerBroken = true;
      worker?.terminate();
      worker = null;
      for (const [id, req] of pending) {
        const w = waiting.get(id)!;
        waiting.delete(id);
        pending.delete(id);
        inline(req.data, req.prediction, req.params).then(w.resolve, w.reject);
      }
    };
  }
  return worker;
}

function inline(data: DataBundle, prediction: Prediction, params: ScenarioParams): Promise<ScenarioResult> {
  return new Promise((resolve, reject) =>
    setTimeout(() => {
      try {
        resolve(computeScenario(data, prediction, params));
      } catch (e) {
        reject(e instanceof Error ? e : new Error(String(e)));
      }
    }, 16),
  );
}

export function computeScenarioAsync(data: DataBundle, prediction: Prediction, params: ScenarioParams): Promise<ScenarioResult> {
  const started = typeof performance !== 'undefined' ? performance.now() : 0;
  const done = (r: ScenarioResult) => {
    if (import.meta.env?.DEV && typeof performance !== 'undefined')
      console.info(`[olpark] 계산 완료 ${Math.round(performance.now() - started)}ms · 후보 ${r.evaluated}개`);
    return r;
  };
  const w = getWorker();
  if (!w) return inline(data, prediction, params).then(done);
  const id = ++seq;
  return new Promise<ScenarioResult>((resolve, reject) => {
    waiting.set(id, { resolve, reject });
    pending.set(id, { data, prediction, params });
    w.postMessage({ id, data, prediction, params });
  }).then(done);
}
