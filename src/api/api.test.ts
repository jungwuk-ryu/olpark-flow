import { afterEach, describe, expect, it, vi } from 'vitest';
import sample from './testing/server-bundle.sample.json';
import { fromServer, loadDataBundle, type ServerBundle } from './index';
import { bundle } from '../data';
import { validateData } from '../data/validate';
import { computeScenario, defaultParams, endTimeOf, venueHasNetwork } from '../model/scenario';

/** 서버(`python -m olpark_demand.server`)가 `GET /api/bundle`에 돌려준 응답에서 공연 3건만 남긴 예시 */
const answer = sample as unknown as ServerBundle;

const respond = (body: unknown, status = 200) =>
  vi.fn(async (_url: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify(body), { status })) as unknown as typeof fetch;

afterEach(() => vi.restoreAllMocks());

describe('서버 응답 예시', () => {
  const data = fromServer(answer);

  it('약속 문서 규칙 위반이 없다', () => {
    expect(validateData(data)).toEqual([]);
  });

  it('공연마다 예측이 한 건씩 있고 과거 유사 공연에 가명을 쓴다', () => {
    const ids = data.events.events.map((e) => e.id);
    expect(data.predictions.predictions.map((p) => p.eventId)).toEqual(ids);
    for (const p of data.predictions.predictions) for (const s of p.similarEvents) expect(s.name).toMatch(/^공연 [A-Z]+$/);
  });

  it('길·주차·가정값은 화면에 묶인 파일을 쓰고, 공지 통제는 비운다', () => {
    expect(data.network).toBe(bundle.network);
    expect(data.routes).toBe(bundle.routes);
    expect(data.parking).toBe(bundle.parking);
    expect(data.assumptions).toBe(bundle.assumptions);
    expect(data.notices.notices).toEqual([]);
    expect(data.source).toMatchObject({ kind: 'server', calendar: 'api', fetchedAt: '2026-11-26T17:00:00+09:00', leftOut: 0 });
  });

  it('KSPO DOME 공연은 지하철 이용 인원만 역 방면 길로 보내 끝까지 계산된다', () => {
    const event = data.events.events.find((e) => venueHasNetwork(data, e.venueId))!;
    const p = data.predictions.predictions.find((x) => x.eventId === event.id)!;
    const s = computeScenario(data, p, defaultParams(p));
    const m = s.current.totals.modePeople;
    // 관객 9,956명 중 42.3%가 지하철, 19.3%가 차량, 나머지는 버스·도보·픽업
    expect(m.station).toBeCloseTo(p.attendance!.value * p.rail!.ratio, 6);
    expect(m.vehicle).toBeCloseTo(p.attendance!.value * p.vehicle!.ratio, 6);
    expect(m.station + m.vehicle + m.other).toBeCloseTo(p.attendance!.value, 6);
    expect(m.other).toBeGreaterThan(3000);
    expect(s.range.stationLow).toBe(p.rail!.peopleLow);
    expect(s.range.stationHigh).toBe(p.rail!.peopleHigh);
    expect(s.range.low.totals.peakQueueTotal).toBeLessThanOrEqual(s.best.result.totals.peakQueueTotal);
    expect(s.range.high.totals.peakQueueTotal).toBeGreaterThanOrEqual(s.best.result.totals.peakQueueTotal);
    expect(s.current.totals.clearMinute).not.toBeNull();
    expect(s.best.result.totals.burdenPersonMinutes).toBeLessThanOrEqual(s.current.totals.burdenPersonMinutes);
  });

  it('종료 시각 범위는 예상 시각 앞뒤 23분이다 (표준편차 17.9분의 80% 범위)', () => {
    const event = data.events.events.find((e) => e.venueId === 'kspo-dome')!;
    const p = data.predictions.predictions.find((x) => x.eventId === event.id)!;
    expect(endTimeOf(event, p)).toEqual({
      expectedAt: event.endAt,
      window: { earlyMinutes: -23, lateMinutes: 23 },
      sdMinutes: 17.9,
      interval: 'p10-p90',
      entered: false,
    });
  });

  it('역 승차 반응을 가려낼 수 없는 공연장은 지하철·차량·종료 시각 범위가 비어 있다', () => {
    const p = data.predictions.predictions.find((x) => x.eventId.startsWith('woori-'))!;
    expect(p.rail).toBeNull();
    expect(p.vehicle).toBeNull();
    expect(p.endTime).toBeNull();
    expect(p.directionShare).toBeNull();
  });
});

describe('loadDataBundle', () => {
  it('서버의 /api/bundle을 불러 공연과 예측을 받는다', async () => {
    const fetcher = respond(answer);
    const data = await loadDataBundle(fetcher);
    expect(String(vi.mocked(fetcher).mock.calls[0]![0])).toBe('/api/bundle');
    expect(data.source?.kind).toBe('server');
    expect(data.events.events).toEqual(answer.events.events);
    expect(data.predictions.model.name).toBe('olpark-event-response');
  });

  it('저장해 둔 달력으로 만든 응답이면 그 사실과 안내를 전한다', async () => {
    const note = '공원 달력 API가 답하지 않아 저장해 둔 달력으로 만들었습니다.';
    const data = await loadDataBundle(respond({ ...answer, source: { ...answer.source, calendar: 'stored', note } }));
    expect(data.source).toMatchObject({ kind: 'server', calendar: 'stored', note });
  });

  it.each([
    ['서버가 오류를 돌려주면', () => respond({ error: '예측을 아직 만들지 못했습니다.' }, 503)],
    ['응답 모양이 다르면', () => respond({ events: [] })],
    [
      '서버에 닿지 않으면',
      () =>
        vi.fn(async () => {
          throw new TypeError('Failed to fetch');
        }) as unknown as typeof fetch,
    ],
  ])('%s 시연용 목업을 돌려주고 그 사실을 알린다', async (_name, make) => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const data = await loadDataBundle(make());
    expect(data.source?.kind).toBe('mock');
    expect(data.source?.note).toContain('시연용 목업');
    expect(data.events).toBe(bundle.events);
    expect(data.predictions).toBe(bundle.predictions);
    expect(warn).toHaveBeenCalledTimes(1);
  });
});
