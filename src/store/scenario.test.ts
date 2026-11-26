import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { bundle } from '../data';
import { useScenario } from './scenario';

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe('시나리오 상태', () => {
  it('데이터를 받으면 길 데이터가 있는 첫 공연을 골라 계산한다', async () => {
    useScenario.getState().init(bundle);
    expect(useScenario.getState().status).toBe('computing');
    await vi.runAllTimersAsync();
    const s = useScenario.getState();
    expect(s.eventId).toBe('kspo-20261121-a');
    expect(s.status).toBe('ready');
    expect(s.result).not.toBeNull();
  });

  it('조건을 바꾸면 결과는 그대로 두고 "다시 계산 필요"가 된다', async () => {
    useScenario.getState().init(bundle);
    await vi.runAllTimersAsync();
    const before = useScenario.getState().result;
    useScenario.getState().setParam('compliance', 0.3);
    expect(useScenario.getState().status).toBe('dirty');
    expect(useScenario.getState().result).toBe(before);

    useScenario.getState().recompute();
    await vi.runAllTimersAsync();
    const after = useScenario.getState();
    expect(after.status).toBe('ready');
    expect(after.result).not.toBe(before);
    expect(after.result!.params.compliance).toBe(0.3);
  });

  it('게시된 공연이 없으면 계산하지 않고 그 사실을 알린다', () => {
    useScenario.getState().init({ ...bundle, events: { schemaVersion: 1, events: [] }, predictions: { ...bundle.predictions, predictions: [] } });
    const s = useScenario.getState();
    expect(s.status).toBe('no-events');
    expect(s.eventId).toBeNull();
    expect(s.result).toBeNull();
  });

  it('실제 종료 시각은 공연별로 넣고 지울 수 있고, 계산 결과는 그대로 둔다', async () => {
    useScenario.getState().init(bundle);
    await vi.runAllTimersAsync();
    const before = useScenario.getState().result;
    useScenario.getState().setEndEntered('kspo-20261121-a', '2026-11-21T21:52:00+09:00');
    expect(useScenario.getState().endEntered).toEqual({ 'kspo-20261121-a': '2026-11-21T21:52:00+09:00' });
    expect(useScenario.getState().result).toBe(before);
    expect(useScenario.getState().status).toBe('ready');
    useScenario.getState().setEndEntered('kspo-20261121-a', null);
    expect(useScenario.getState().endEntered).toEqual({});
  });

  it('길 데이터가 없는 공연장은 계산하지 않고 안내한다', async () => {
    useScenario.getState().init(bundle);
    await vi.runAllTimersAsync();
    useScenario.getState().selectEvent('olympichall-20261122-b');
    await vi.runAllTimersAsync();
    expect(useScenario.getState().status).toBe('no-network');
    expect(useScenario.getState().result).toBeNull();
  });
});
