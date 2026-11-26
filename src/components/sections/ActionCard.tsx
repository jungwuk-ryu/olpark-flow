import { useState } from 'react';
import { Card, CardHeader } from '../ui/Card';
import { Button } from '../ui/Button';
import { formatClock, formatClockRange } from '../../lib/format';
import { checklist } from '../../model/brief';
import { selectEvent, selectPrediction, useEndTime, useScenario } from '../../store/scenario';

/** 현장 안내 실행표: 예측이 주는 시각과 인원을 시간순으로 적는다 */
export function ActionCard() {
  const data = useScenario((s) => s.data);
  const event = useScenario(selectEvent);
  const prediction = useScenario(selectPrediction);
  const end = useEndTime();
  const [copied, setCopied] = useState<'ok' | 'fail' | null>(null);

  const steps = data && event && prediction && end ? checklist(event, prediction, end, data.assumptions) : [];
  const when = (s: (typeof steps)[number]) => (s.ranged ? `${formatClock(s.at)} (${formatClockRange(s.earliest, s.latest)})` : formatClock(s.at));

  async function copy() {
    const head = event ? `${event.venueName} ${event.name} 실행표` : '실행표';
    const text = [head, ...steps.map((s) => `${when(s)}\t${s.title}\t${s.detail}`)].join('\n');
    try {
      await navigator.clipboard.writeText(text);
      setCopied('ok');
    } catch {
      setCopied('fail');
    }
    setTimeout(() => setCopied(null), 2000);
  }

  return (
    <Card id="actions" className="flex scroll-mt-[84px] flex-col gap-4 p-6 pt-5">
      <CardHeader
        title="현장 안내 실행표"
        description={end?.entered ? '실제 종료 시각 기준 운영 일정' : '예상 종료 시각 기준 운영 일정 · 시간 범위는 종료 시각의 변동을 반영합니다.'}
        actions={
          <Button icon="copy" onClick={copy} disabled={!steps.length}>
            {copied === 'ok' ? '복사했습니다' : copied === 'fail' ? '복사 실패' : '복사'}
          </Button>
        }
      />
      {!steps.length ? (
        <p className="text-[13px] text-ink-2">공연 선택 후 운영 일정을 확인할 수 있습니다.</p>
      ) : (
        <ol className="flex flex-col">
          {steps.map((s, i) => (
            <li key={s.key} className="grid grid-cols-[150px_20px_minmax(0,1fr)] gap-3">
              <span className="flex flex-col pt-0.5 text-right">
                <span className="text-[16px] leading-[22px] font-bold text-ink">{formatClock(s.at)}</span>
                {s.ranged && <span className="text-[12px] leading-[17px] text-ink-2">{formatClockRange(s.earliest, s.latest)}</span>}
              </span>
              <span className="flex flex-col items-center" aria-hidden="true">
                <span className={`mt-1.5 size-3 rounded-full border-2 ${s.key === 'ready' || s.key === 'end' ? 'border-accent bg-accent' : 'border-accent bg-card'}`} />
                {i < steps.length - 1 && <span className="w-px flex-1 bg-line-strong" />}
              </span>
              <span className={`flex flex-col gap-0.5 ${i < steps.length - 1 ? 'pb-5' : ''}`}>
                <span className="text-[14px] leading-[22px] font-bold text-ink">{s.title}</span>
                <span className="text-[13px] leading-5 text-ink-2">{s.detail}</span>
              </span>
            </li>
          ))}
        </ol>
      )}
      <p className="mt-auto border-t border-line pt-3 text-[12px] leading-[18px] text-ink-2">
        * 시각과 인원은 예측값입니다.
      </p>
    </Card>
  );
}
