import { statusOf } from './status';
import type { Parking, Status, VehicleExit, VehicleExitSeries } from './types';

/**
 * 차량 출구 대기. 출구마다 줄 하나:
 * 통과(t) = min(대기 + 도착, 처리량 × 보정), 대기(t) = 대기(t−1) + 도착(t) − 통과(t)
 */
export class VehicleQueues {
  readonly series: VehicleExitSeries[];
  private readonly exits: VehicleExit[];
  private readonly departures: number[];

  constructor(departures: number[], exits: VehicleExit[], share: Record<string, number>) {
    this.departures = departures;
    this.exits = exits;
    const H = departures.length;
    this.series = exits.map((e) => ({
      exitId: e.id,
      share: share[e.id] ?? 0,
      arrivals: Array(H).fill(0),
      passed: Array(H).fill(0),
      queue: Array(H).fill(0),
      maxQueue: 0,
      clearMinute: 0,
      status: Array<Status>(H).fill('여유'),
    }));
  }

  /** t분을 계산한다. factor(exitId)는 이번 분 처리량에 곱할 값 (교차점 등) */
  step(t: number, factor: (exitId: string) => number = () => 1): void {
    this.exits.forEach((e, i) => {
      const s = this.series[i]!;
      const arr = this.departures[t]! * s.share;
      const cap = e.throughputPerMin * factor(e.id);
      const prev = t > 0 ? s.queue[t - 1]! : 0;
      const pass = Math.min(prev + arr, cap);
      const left = prev + arr - pass;
      s.arrivals[t] = arr;
      s.passed[t] = pass;
      s.queue[t] = left > 1e-9 ? left : 0;
      s.status[t] = statusOf({
        saturation: cap > 0 ? arr / cap : arr > 0 ? Infinity : 0,
        queue: s.queue[t]!,
        areaPerPersonM2: null,
        jamAreaPerPersonM2: 0,
      });
    });
  }

  /** 이 출구에 이번 분 차량이 있었는지 (도착 또는 대기) */
  activeAt(exitId: string, t: number): boolean {
    const s = this.series.find((x) => x.exitId === exitId);
    if (!s || t < 0) return false;
    return s.arrivals[t]! + s.queue[t]! > 1e-9;
  }

  finish(): { exits: VehicleExitSeries[]; queueVehicleMinutes: number } {
    const H = this.departures.length;
    let total = 0;
    for (const s of this.series) {
      s.maxQueue = Math.max(0, ...s.queue);
      let last = -1;
      s.queue.forEach((q, t) => {
        total += q;
        if (q > 1e-6) last = t;
      });
      s.clearMinute = last === -1 ? 0 : last === H - 1 ? null : last + 2;
    }
    return { exits: this.series, queueVehicleMinutes: total };
  }
}

export function simulateVehicleExits(p: {
  departures: number[];
  exits: VehicleExit[];
  share: Record<string, number>;
}): { exits: VehicleExitSeries[]; queueVehicleMinutes: number } {
  const q = new VehicleQueues(p.departures, p.exits, normalize(p.share, p.exits));
  for (let t = 0; t < p.departures.length; t++) q.step(t);
  return q.finish();
}

/** 출구 배정을 주지 않으면 주차장마다 용량을 연결된 출구에 똑같이 나눈 비율을 쓴다 */
export function defaultExitShare(parking: Parking): Record<string, number> {
  const acc: Record<string, number> = Object.fromEntries(parking.exits.map((e) => [e.id, 0]));
  for (const lot of parking.lots) {
    const ids = lot.exitIds.filter((id) => id in acc);
    for (const id of ids) acc[id] = acc[id]! + lot.capacity / ids.length;
  }
  return normalize(acc, parking.exits);
}

export function normalize(share: Record<string, number>, exits: VehicleExit[]): Record<string, number> {
  const sum = exits.reduce((s, e) => s + Math.max(0, share[e.id] ?? 0), 0);
  return Object.fromEntries(
    exits.map((e) => [e.id, sum > 0 ? Math.max(0, share[e.id] ?? 0) / sum : 1 / exits.length]),
  );
}
