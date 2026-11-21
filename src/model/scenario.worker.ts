/// <reference lib="webworker" />
import { computeScenario, type ScenarioParams } from './scenario';
import type { DataBundle, Prediction } from '../data/contract';

export interface WorkerRequest {
  id: number;
  data: DataBundle;
  prediction: Prediction;
  params: ScenarioParams;
}

self.onmessage = (e: MessageEvent<WorkerRequest>) => {
  const { id, data, prediction, params } = e.data;
  try {
    const result = computeScenario(data, prediction, params);
    self.postMessage({ id, ok: true, result });
  } catch (err) {
    self.postMessage({ id, ok: false, error: err instanceof Error ? err.message : String(err) });
  }
};
