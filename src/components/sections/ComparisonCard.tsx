import { useState } from 'react';
import { Card, CardHeader, Pending } from '../ui/Card';
import { Button } from '../ui/Button';
import { formatNumber } from '../../lib/format';
import { useScenario } from '../../store/scenario';
import type { Totals } from '../../engine/types';

interface Row {
  label: string;
  current: string;
  best: string;
  change: string;
  better: boolean | null;
}

function pct(cur: number, next: number): { text: string; better: boolean | null } {
  if (cur === 0) return { text: next === 0 ? '—' : '신규', better: next === 0 ? null : false };
  const r = (next - cur) / cur;
  if (Math.abs(r) < 0.005) return { text: '—', better: null };
  return { text: `${r > 0 ? '+' : '−'}${formatNumber(Math.abs(r) * 100)}%`, better: r < 0 };
}

function rows(c: Totals, b: Totals, from: 'A' | 'B' | 'C', hasVehicle: boolean): Row[] {
  const walkC = c.queueMinutesTotal + c.detourMinutesTotal;
  const walkB = b.queueMinutesTotal + b.detourMinutesTotal;
  const out: Row[] = [
    { label: '최대 대기 인원', current: `${formatNumber(c.peakQueueTotal)}명`, best: `${formatNumber(b.peakQueueTotal)}명`, ...split(pct(c.peakQueueTotal, b.peakQueueTotal)) },
    { label: '대기시간 합계', current: `${formatNumber(c.queueMinutesTotal)}사람·분`, best: `${formatNumber(b.queueMinutesTotal)}사람·분`, ...split(pct(c.queueMinutesTotal, b.queueMinutesTotal)) },
    { label: '우회 보행시간', current: `${formatNumber(c.detourMinutesTotal)}사람·분`, best: `${formatNumber(b.detourMinutesTotal)}사람·분`, ...split(pct(c.detourMinutesTotal, b.detourMinutesTotal)) },
    { label: '대기 + 추가 보행', current: `${formatNumber(walkC)}사람·분`, best: `${formatNumber(walkB)}사람·분`, ...split(pct(walkC, walkB)) },
  ];
  if (hasVehicle)
    out.push({
      label: '차량 대기 (탑승 인원 환산)',
      current: `${formatNumber(c.vehicleQueuePersonMinutes)}사람·분`,
      best: `${formatNumber(b.vehicleQueuePersonMinutes)}사람·분`,
      ...split(pct(c.vehicleQueuePersonMinutes, b.vehicleQueuePersonMinutes)),
    });
  const clearText = (v: number | null) => (v === null ? '90분 넘음' : `${v}분`);
  const clearDiff = c.clearMinute !== null && b.clearMinute !== null ? b.clearMinute - c.clearMinute : null;
  out.push({
    label: '대기 해소',
    current: clearText(c.clearMinute),
    best: clearText(b.clearMinute),
    change: clearDiff === null || clearDiff === 0 ? '—' : `${clearDiff > 0 ? '+' : '−'}${Math.abs(clearDiff)}분`,
    better: clearDiff === null || clearDiff === 0 ? null : clearDiff < 0,
  });
  const sc = c.directionShareActual[from] * 100;
  const sb = b.directionShareActual[from] * 100;
  const dp = Math.round(sb - sc);
  out.push({
    label: `${from}방면 비중`,
    current: `${formatNumber(sc)}%`,
    best: `${formatNumber(sb)}%`,
    change: dp === 0 ? '—' : `${dp > 0 ? '+' : '−'}${Math.abs(dp)}%p`,
    better: null,
  });
  return out;
}
const split = (p: { text: string; better: boolean | null }) => ({ change: p.text, better: p.better });

export function ComparisonCard() {
  const result = useScenario((s) => s.result);
  const status = useScenario((s) => s.status);
  const [copied, setCopied] = useState<'ok' | 'fail' | null>(null);

  const from = result?.best.plan.guidance.moves[0]?.from ?? 'A';
  const data = result ? rows(result.current.totals, result.best.result.totals, from, !!result.current.vehicle) : [];

  async function copy() {
    const text = ['지표\t현재 운영안\t분산 운영안\t변화', ...data.map((r) => `${r.label}\t${r.current}\t${r.best}\t${r.change}`)].join('\n');
    try {
      await navigator.clipboard.writeText(text);
      setCopied('ok');
    } catch {
      setCopied('fail');
    }
    setTimeout(() => setCopied(null), 2000);
  }

  return (
    <Card className="flex flex-col overflow-hidden">
      <CardHeader
        className="px-6 pt-5 pb-4"
        title="운영안 비교"
        description="같은 관객 수와 통로 조건에서 계산한 결과"
        actions={
          <Button icon="copy" onClick={copy} disabled={!result}>
            {copied === 'ok' ? '복사했습니다' : copied === 'fail' ? '복사 실패' : '안내표 복사'}
          </Button>
        }
      />
      {!result ? (
        <div className="px-6 pb-6">
          <Pending label={status === 'no-network' ? '길 데이터 준비 중' : '계산 중…'} height={300} />
        </div>
      ) : (
        <table className="w-full text-[14px]">
          <thead>
            <tr className="border-y border-line bg-subtle text-[12px] text-ink-2">
              <th scope="col" className="px-6 py-2.5 text-left font-medium">지표</th>
              <th scope="col" className="px-4 py-2.5 text-right font-medium">현재 운영안</th>
              <th scope="col" className="px-4 py-2.5 text-right font-medium">분산 운영안</th>
              <th scope="col" className="px-6 py-2.5 text-right font-medium">변화</th>
            </tr>
          </thead>
          <tbody>
            {data.map((r) => (
              <tr key={r.label} className="border-b border-line last:border-b-0">
                <th scope="row" className="px-6 py-3 text-left font-normal">{r.label}</th>
                <td className="px-4 py-3 text-right">{r.current}</td>
                <td className="px-4 py-3 text-right font-bold text-accent">{r.best}</td>
                <td className={`px-6 py-3 text-right text-[12px] font-medium ${r.better === true ? 'text-accent' : r.better === false ? 'text-bottleneck' : 'text-ink-2'}`}>
                  {r.change}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="mt-auto flex items-center justify-between border-t border-line px-6 py-3 text-[12px] text-ink-2">
        <span>사람·분 = 인원 × 시간 · 우회 보행 증가는 분산 안내의 비용입니다</span>
        <Button variant="ghost" icon="clipboard" disabled title="7단계에서 연결">
          운영안 기록
        </Button>
      </div>
    </Card>
  );
}
