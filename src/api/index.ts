import { bundle } from '../data';
import type { AudienceDecision, AudienceType, DataBundle, DataSource, Evening, Events, Evidence, Predictions } from '../data/contract';

/**
 * 데이터 접근은 이 파일로만 한다.
 *
 * 공연 목록과 예측은 서버가 돌려준다 (`ml/`에서 `python -m olpark_demand.server`).
 * 서버는 올림픽공원 공식 달력 API를 호출해 앞으로 3주의 공연을 받고 수요 모델로 예측을 만든다.
 * 길·주차·가정값은 화면에 묶인 src/data의 파일을 쓴다. 응답 모양은 docs/data-contract.md와 같다.
 *
 * 서버에 닿지 않으면 src/data의 시연용 목업을 돌려주고, 그 사실을 source.kind = 'mock'으로 알린다.
 */

const API_BASE = String(import.meta.env?.VITE_API_BASE ?? '/api').replace(/\/$/, '');

/** `GET /api/bundle`의 응답 */
export interface ServerBundle {
  events: Events;
  predictions: Predictions;
  evenings?: Evening[];
  evidence?: Evidence | null;
  source?: Partial<DataSource> | null;
}

function isServerBundle(v: unknown): v is ServerBundle {
  const b = v as ServerBundle | null;
  return !!b && Array.isArray(b.events?.events) && Array.isArray(b.predictions?.predictions) && typeof b.predictions.generatedAt === 'string';
}

/**
 * 서버 응답에 길·주차·가정값을 붙여 화면이 쓰는 묶음으로 만든다.
 * 수요 모델은 공지 통제를 뽑지 않으므로 통제 목록은 비운다.
 */
export function fromServer(answer: ServerBundle): DataBundle {
  const s = answer.source ?? {};
  const source: DataSource = {
    kind: 'server',
    calendar: s.calendar === 'stored' ? 'stored' : 'api',
    fetchedAt: s.fetchedAt ?? undefined,
    provider: s.provider ?? undefined,
    leftOut: s.leftOut ?? 0,
    note: s.note ?? undefined,
  };
  return {
    ...bundle,
    source,
    events: answer.events,
    predictions: answer.predictions,
    evenings: Array.isArray(answer.evenings) ? answer.evenings : [],
    evidence: answer.evidence ?? null,
    notices: { schemaVersion: 1, notices: [] },
  };
}

/** 서버에 닿지 않을 때 쓰는 시연용 목업 묶음 */
export function mockBundle(): DataBundle {
  return { ...bundle, source: { kind: 'mock', note: '서버에서 공연과 예측을 받지 못해 시연용 목업 데이터를 보여줍니다.' } };
}

/** 화면 첫 로딩에 필요한 데이터 전부 */
export async function loadDataBundle(fetcher: typeof fetch = fetch): Promise<DataBundle> {
  try {
    return await fetchServerBundle(fetcher);
  } catch (e) {
    console.warn('[olpark] 서버에서 공연과 예측을 받지 못했습니다:', e instanceof Error ? e.message : String(e));
    return mockBundle();
  }
}

export async function fetchServerBundle(fetcher: typeof fetch = fetch, signal?: AbortSignal): Promise<DataBundle> {
  const res = await fetcher(`${API_BASE}/bundle`, { headers: { Accept: 'application/json' }, signal });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const answer: unknown = await res.json();
  if (!isServerBundle(answer)) throw new Error('응답 모양이 다름');
  return fromServer(answer);
}

export async function audienceDecision(programme: string, action: 'resolve' | 'manual' | 'retry', options: {
  signal?: AbortSignal; label?: AudienceType; token?: string;
} = {}): Promise<AudienceDecision> {
  const response = await fetch(`${API_BASE}/audience/${encodeURIComponent(programme)}`, {
    method: 'POST', signal: options.signal,
    headers: { 'Content-Type': 'application/json', ...(options.token ? { Authorization: 'Bearer ' + options.token } : {}) },
    body: JSON.stringify({ action, label: options.label }),
  });
  if (!response.ok) throw new Error(response.status === 403 ? '관리자 키를 확인하세요.' : '관객층 정보를 불러오지 못했습니다.');
  return response.json();
}
