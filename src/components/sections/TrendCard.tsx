import { Area, CartesianGrid, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { Card, CardHeader, Pending } from '../ui/Card';
import { formatClock, formatClockRange, formatNumber } from '../../lib/format';
import { clockEnvelope, clockTimes, FIXED_END, valueAfterEnd } from '../../engine';
import { useEndTime, useScenario } from '../../store/scenario';

const MAX_MINUTE = 60;
const MINUTE_MS = 60_000;
const QUARTER_MS = 15 * MINUTE_MS;
/** 종료 시각 범위 띠 색 (경로 C의 청회색). 인원 범위 띠(강조색)와 구분한다 */
const END_BAND = 'var(--color-route-c)';

/** x(예상 종료 기준 분) 범위 안의 15분 단위 시계 눈금 */
function quarterTicks(endMs: number, from: number, to: number): number[] {
  const ticks: number[] = [];
  for (let t = Math.ceil((endMs + from * MINUTE_MS) / QUARTER_MS) * QUARTER_MS; t <= endMs + to * MINUTE_MS; t += QUARTER_MS)
    ticks.push((t - endMs) / MINUTE_MS);
  return ticks;
}

const relative = (x: number) => (x === 0 ? '예상 종료 시각' : x > 0 ? `예상 종료 +${x}분` : `예상 종료 ${-x}분 전`);

export function TrendCard() {
  const result = useScenario((s) => s.result);
  const status = useScenario((s) => s.status);
  const minute = useScenario((s) => s.minute);
  const end = useEndTime();

  // 종료 시각이 움직일 수 있는 만큼 시간축을 앞뒤로 넓힌다
  const window = end?.window ?? FIXED_END;
  const from = Math.min(0, window.earlyMinutes);
  const to = MAX_MINUTE + Math.max(0, window.lateMinutes);
  const endMs = end ? Date.parse(end.expectedAt) : NaN;
  const clock = (x: number) => (Number.isFinite(endMs) ? formatClock(endMs + x * MINUTE_MS) : `+${x}분`);

  const slide = result && end?.window ? clockEnvelope(result.best.result.queueTotal, window, from, to) : null;
  const rows = result
    ? Array.from({ length: to - from + 1 }, (_, i) => {
        const x = from + i;
        return {
          x,
          current: valueAfterEnd(result.current.queueTotal, x),
          best: valueAfterEnd(result.best.result.queueTotal, x),
          band: [valueAfterEnd(result.range.low.queueTotal, x), valueAfterEnd(result.range.high.queueTotal, x)] as [number, number],
          ...(slide ? { slide: [slide[i]!.low, slide[i]!.high] as [number, number] } : {}),
        };
      })
    : [];
  const nowBest = result ? valueAfterEnd(result.best.result.queueTotal, minute) : 0;
  const endRange = end?.window ? clockTimes(end.expectedAt, 0, end.window) : null;
  const ticks = Number.isFinite(endMs) ? quarterTicks(endMs, from, to) : [0, 15, 30, 45, 60];

  return (
    <Card className="flex flex-col gap-3 p-6 pt-5">
      <CardHeader
        title="대기 인원 추이"
        description="퇴장 시작 후 시간에 따라 쌓이는 예상 대기열 · 모든 병목 합계"
        actions={
          <div className="flex items-center gap-4 text-[12px] text-ink-2">
            <span className="flex items-center gap-1.5">
              <svg width="20" height="4" aria-hidden="true">
                <path d="M1 2h18" stroke="#9AA29D" strokeWidth="2.5" strokeDasharray="3 3" strokeLinecap="round" />
              </svg>
              현재 운영안
            </span>
            <span className="flex items-center gap-1.5">
              <svg width="20" height="4" aria-hidden="true">
                <path d="M1 2h18" stroke="var(--color-accent)" strokeWidth="2.5" strokeLinecap="round" />
              </svg>
              분산 운영안
            </span>
            <span className="flex items-center gap-1.5">
              <span className="h-2.5 w-4 rounded-sm bg-accent/10 ring-1 ring-accent/25" aria-hidden="true" />
              인원 범위
            </span>
            {slide && (
              <span className="flex items-center gap-1.5">
                <span className="h-2.5 w-4 rounded-sm bg-route-c/25 ring-1 ring-route-c/60" aria-hidden="true" />
                종료 시각 범위
              </span>
            )}
          </div>
        }
      />
      {!result ? (
        <Pending label={status === 'no-network' ? '길 데이터 준비 중' : status === 'no-events' ? '게시된 공연 없음' : '계산 중…'} height={260} />
      ) : (
        <div
          className="h-[260px]"
          role="img"
          aria-label={
            `대기 인원 추이. 현재 운영안 최대 ${formatNumber(result.current.totals.peakQueueTotal)}명, 분산 운영안 최대 ${formatNumber(result.best.result.totals.peakQueueTotal)}명.` +
            (endRange ? ` 종료 시각은 ${formatClockRange(endRange.earliest, endRange.latest)} 사이에서 움직일 수 있습니다.` : '')
          }
        >
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={rows} margin={{ top: 12, right: 8, bottom: 0, left: 0 }}>
              <CartesianGrid vertical={false} stroke="var(--color-line)" />
              <XAxis
                dataKey="x"
                type="number"
                domain={[from, to]}
                ticks={ticks}
                tickFormatter={(v) => clock(Number(v))}
                tick={{ fontSize: 11, fill: 'var(--color-ink-2)' }}
                axisLine={{ stroke: 'var(--color-line)' }}
                tickLine={false}
              />
              <YAxis
                width={48}
                tickFormatter={(v) => formatNumber(v)}
                tick={{ fontSize: 11, fill: 'var(--color-ink-2)' }}
                axisLine={false}
                tickLine={false}
              />
              <Tooltip
                formatter={(v, name) => {
                  if (Array.isArray(v)) {
                    const text = `${formatNumber(Number(v[0]))}~${formatNumber(Number(v[1]))}명`;
                    return name === 'slide' ? [text, '종료 시각에 따라'] : [text, '인원 범위'];
                  }
                  return [`${formatNumber(Number(v))}명`, name === 'current' ? '현재 운영안' : '분산 운영안'];
                }}
                labelFormatter={(x) => `${clock(Number(x))} · ${relative(Number(x))}`}
                contentStyle={{ borderRadius: 8, border: '1px solid var(--color-line)', fontSize: 12 }}
              />
              {slide && <Area dataKey="slide" stroke="none" fill={END_BAND} fillOpacity={0.2} isAnimationActive={false} activeDot={false} />}
              <Area dataKey="band" stroke="none" fill="var(--color-accent)" fillOpacity={0.08} isAnimationActive={false} activeDot={false} />
              <Area dataKey="best" stroke="var(--color-accent)" strokeWidth={2.5} fill="var(--color-accent)" fillOpacity={0.1} isAnimationActive={false} />
              <Line dataKey="current" stroke="#9AA29D" strokeWidth={2} strokeDasharray="5 5" dot={false} isAnimationActive={false} />
              {slide && <ReferenceLine x={0} stroke="var(--color-line-strong)" label={{ value: '예상 종료', position: 'insideTopLeft', fontSize: 11, fill: 'var(--color-ink-2)' }} />}
              <ReferenceLine x={minute} stroke="var(--color-ink)" strokeDasharray="3 3" />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}
      <div className="flex items-start justify-between gap-4 text-[12px] leading-[18px] text-ink-2">
        <span>
          대기열은 통로별 유입·통과를 1분 단위로 계산한 결과입니다.
          {result && (
            <>
              {' '}
              인원 범위는 지하철 이용 인원 {formatNumber(result.range.stationLow)}~{formatNumber(result.range.stationHigh)}명(80% 예측 범위)으로 다시 계산한 값입니다.
            </>
          )}
          {endRange && (
            <>
              {' '}
              종료 시각 범위는 공연이 {formatClockRange(endRange.earliest, endRange.latest)} 사이에 끝날 때 분산 운영안의 대기가 닿는 폭입니다. 줄의 크기는 같고 시각만 움직입니다.
            </>
          )}
        </span>
        {result && (
          <span className="shrink-0 font-medium text-ink">
            {clock(minute)} · 종료 +{minute}분 · {formatNumber(nowBest)}명
          </span>
        )}
      </div>
    </Card>
  );
}
