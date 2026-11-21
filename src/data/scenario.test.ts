import { describe, expect, it } from 'vitest';
import { bundle } from './index';
import { validateData } from './validate';
import { simulate } from '../engine/simulate';
import { optimize } from '../engine/optimize';
import type { SimulationInput } from '../engine/types';

/**
 * `pnpm validate:data`가 돌리는 파일. src/data의 실제 파일이 약속 문서를 지키는지,
 * 그리고 그 파일로 엔진이 끝까지 계산되는지 확인한다.
 */
describe('src/data 파일 검사', () => {
  it('약속 문서 규칙 위반이 없다', () => {
    expect(validateData(bundle)).toEqual([]);
  });
});

describe('KSPO DOME · 공연 A 시연 시나리오', () => {
  const p = bundle.predictions.predictions.find((x) => x.eventId === 'kspo-20261121-a')!;
  const input: SimulationInput = {
    network: bundle.network,
    routes: bundle.routes,
    assumptions: bundle.assumptions,
    attendance: p.attendance!.value,
    egress: { kind: 'gamma', totalMinutes: p.egressCurve!.totalMinutes, peakMinute: p.egressCurve!.peakMinute },
    directionShare: p.directionShare!,
    compliance: 0.6,
    horizonMinutes: 90,
    vehicle: { parking: bundle.parking, ratio: p.vehicle!.ratio },
  };

  it('현재 운영안은 A 경로에 줄이 생기고 90분 안에 풀린다', () => {
    const r = simulate(input);
    expect(r.totals.maxQueue).toBeGreaterThan(300);
    expect(r.totals.clearMinute).not.toBeNull();
    const L = r.ledger;
    const last = r.minutes - 1;
    expect(L.departed[last]!).toBeCloseTo(L.arrived[last]! + L.inTransit[last]! + L.queued[last]!, 6);
  });

  it('분산안 탐색은 대기 + 추가 보행 합계를 20% 이상 줄인다', () => {
    const r = optimize(input, { staff: 6, maxDetourPerPersonMinutes: 10 });
    const cur = r.current.totals.burdenPersonMinutes;
    const best = r.best.result.totals.burdenPersonMinutes;
    expect(best).toBeLessThan(cur * 0.8);
    expect(r.best.plan.guidance.moves.length).toBeGreaterThan(0);
  });

  it('공지 통제를 적용하면 C 방면이 빠지고 남측 통로가 좁아진다', () => {
    const closed = bundle.notices.notices.filter((n) => n.effect === 'closed').flatMap((n) => n.edgeIds);
    const narrowed = Object.fromEntries(
      bundle.notices.notices.filter((n) => n.effect === 'narrowed').flatMap((n) => n.edgeIds.map((id) => [id, n.widthM!])),
    );
    const r = simulate({ ...input, blockedEdgeIds: closed, edgeWidthOverrides: narrowed });
    expect(r.unavailableDirections).toEqual(['C']);
    expect(r.edges['e-south-center']!.capacityPerMin).toBeCloseTo(4 * 82, 6);
  });
});
