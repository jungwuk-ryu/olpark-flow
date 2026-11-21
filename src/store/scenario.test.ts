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

  it('길 데이터가 없는 공연장은 계산하지 않고 안내한다', async () => {
    useScenario.getState().init(bundle);
    await vi.runAllTimersAsync();
    useScenario.getState().selectEvent('olympichall-20261122-b');
    await vi.runAllTimersAsync();
    expect(useScenario.getState().status).toBe('no-network');
    expect(useScenario.getState().result).toBeNull();
  });
});
