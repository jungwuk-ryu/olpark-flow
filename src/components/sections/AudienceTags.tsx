import { useEffect, useRef, useState } from 'react';
import { audienceDecision, fetchServerBundle } from '../../api';
import { AUDIENCES, type AudienceDecision, type AudienceType } from '../../data/contract';
import { selectEvent, selectPrediction, useScenario } from '../../store/scenario';
import { Card } from '../ui/Card';
import { Icon } from '../ui/Icon';

const labels = Object.entries(AUDIENCES) as [AudienceType, string][];
const statusText: Record<AudienceDecision['status'], string> = {
  idle: '분류 대기', pending: 'AI 추측 중', ai: 'AI 추측', manual: '관리자 선택', catalog: '기존 분류',
  failed: 'AI 추측 실패', unconfigured: 'AI 키 미설정',
};
// Held only while the dashboard is open.
let adminToken = '';

export function AudienceTags({ programme, initial }: { programme?: string; initial?: string | null }) {
  const [state, setState] = useState<AudienceDecision | null>(null);
  const [choice, setChoice] = useState<AudienceType | 'retry' | null>(null);
  const [key, setKey] = useState('');
  const [error, setError] = useState('');
  const [offline, setOffline] = useState(false);
  const [saving, setSaving] = useState(false);
  const [revision, setRevision] = useState(0);
  const active = useRef(0);
  const saveRequest = useRef<AbortController | null>(null);

  useEffect(() => () => saveRequest.current?.abort(), []);
  useEffect(() => {
    if (!programme) return;
    const controller = new AbortController();
    const signal = controller.signal;
    let timer: ReturnType<typeof setTimeout>;
    let reading = false;
    async function sync() {
      if (reading || signal.aborted) return;
      clearTimeout(timer);
      const ticket = active.current;
      reading = true;
      let wait = 1200;
      try {
        const result = await audienceDecision(programme!, 'resolve', { signal });
        if (signal.aborted || ticket !== active.current) return;
        setOffline(false);
        setState(result);
        const current = useScenario.getState();
        const brief = selectPrediction(current)?.brief;
        if (result.label && result.applied && (!brief?.labelled || brief.audienceType !== result.label)) {
          const data = await fetchServerBundle(fetch, signal);
          if (!signal.aborted && ticket === active.current && selectEvent(useScenario.getState())?.programmeId === programme) {
            useScenario.getState().updateForecasts(data);
          }
        }
        if (result.status !== 'pending' && result.applied) wait = 15_000;
      } catch {
        if (!signal.aborted && ticket === active.current) setOffline(true);
        wait = 5000;
      } finally {
        reading = false;
        if (!signal.aborted) timer = setTimeout(sync, wait);
      }
    }
    void sync();
    window.addEventListener('focus', sync);
    return () => {
      controller.abort();
      clearTimeout(timer);
      window.removeEventListener('focus', sync);
    };
  }, [programme, revision]);

  async function save(value: AudienceType | 'retry', token: string) {
    if (!programme || saving) return;
    const controller = new AbortController();
    saveRequest.current = controller;
    active.current++;
    setSaving(true);
    setError('');
    try {
      const data = await audienceDecision(programme, value === 'retry' ? 'retry' : 'manual', {
        token, label: value === 'retry' ? undefined : value, signal: controller.signal,
      });
      if (controller.signal.aborted) return;
      active.current++;
      adminToken = token;
      setState(data);
      setChoice(null);
      setKey('');
    } catch (issue) {
      if (controller.signal.aborted) return;
      adminToken = '';
      setChoice(value);
      setError(issue instanceof Error ? issue.message : '저장하지 못했습니다.');
    } finally {
      if (!controller.signal.aborted) {
        setSaving(false);
        setRevision((n) => n + 1);
      }
    }
  }

  function choose(value: AudienceType | 'retry') {
    setError('');
    if (adminToken) void save(value, adminToken);
    else setChoice(value);
  }

  const selected = state ? state.label : initial;
  const pending = state?.status === 'pending';
  const status = state ? statusText[state.status] : programme ? '확인 중' : '시연 데이터';
  return (
    <Card aria-label="공연 관객층" className="flex flex-col gap-3.5 px-6 py-4">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-[15px] font-bold">관객층</h2>
        <div className="flex items-center gap-3 text-[12px] font-medium text-ink-2">
          <span role="status" className="inline-flex items-center gap-1.5">
            {pending ? <span aria-hidden="true" className="size-3 animate-spin rounded-full border-2 border-line border-t-accent motion-reduce:animate-none" />
              : state?.status === 'ai' ? <Icon name="sparkles" size={14} /> : null}
            {saving ? '저장 중' : status}
            {state?.label && !state.applied && ' · 수요 예측 반영 중'}
          </span>
          {state?.status === 'failed' && <button className="cursor-pointer font-bold text-accent underline" onClick={() => choose('retry')}>다시 추측</button>}
        </div>
      </header>
      <div role="group" aria-label="관객층 선택" className="flex flex-wrap gap-2">
        {labels.map(([value, text]) => {
          const checked = selected === value;
          return <button key={value} type="button" aria-pressed={checked} disabled={!programme || saving} onClick={() => choose(value)}
            className={`inline-flex min-h-9 items-center gap-1.5 rounded-full border px-3.5 text-[13px] font-medium transition-colors enabled:cursor-pointer
              ${checked ? 'border-accent bg-accent text-white' : 'border-line-strong bg-card text-ink enabled:hover:border-accent enabled:hover:bg-accent-tint'}`}>
            {checked && <span aria-hidden="true">✓</span>}{text}
          </button>;
        })}
      </div>
      {choice && <form className="flex flex-wrap items-center gap-2 border-t border-line pt-3" onSubmit={(e) => {
        e.preventDefault();
        if (key.trim()) void save(choice, key.trim());
      }}>
        <label className="text-[13px] font-semibold" htmlFor="audience-admin-key">관리자 키</label>
        <input id="audience-admin-key" autoFocus autoComplete="off" type="password" required value={key}
          onChange={(e) => setKey(e.target.value)} className="min-h-9 rounded-lg border border-line-strong px-3" />
        <button disabled={saving || !key.trim()} className="min-h-9 cursor-pointer rounded-lg bg-accent px-3 font-semibold text-white disabled:opacity-50">
          {choice === 'retry' ? '다시 추측' : `${AUDIENCES[choice]} 저장`}
        </button>
        <button type="button" disabled={saving} className="min-h-9 cursor-pointer px-2 text-ink-2"
          onClick={() => { setChoice(null); setError(''); setKey(''); }}>취소</button>
      </form>}
      {(error || offline) && <p role="alert" className="text-[12px] text-danger">{error || '서버 연결을 확인하는 중입니다.'}</p>}
    </Card>
  );
}
