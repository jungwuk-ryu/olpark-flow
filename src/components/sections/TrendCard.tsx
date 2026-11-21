import { Area, CartesianGrid, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { Card, CardHeader, Pending } from '../ui/Card';
import { formatNumber } from '../../lib/format';
import { useScenario } from '../../store/scenario';

const MAX_MINUTE = 60;

/** 배열 t번째 = 종료 후 t+1분 시점의 대기. 차트 x는 "종료 후 분" */
const at = (arr: number[], x: number) => (x === 0 ? 0 : (arr[x - 1] ?? 0));

export function TrendCard() {
  const result = useScenario((s) => s.result);
  const status = useScenario((s) => s.status);
  const minute = useScenario((s) => s.minute);

  const rows = result
    ? Array.from({ length: MAX_MINUTE + 1 }, (_, x) => ({
        x,
        current: at(result.current.queueTotal, x),
        best: at(result.best.result.queueTotal, x),
        band: [at(result.range.low.queueTotal, x), at(result.range.high.queueTotal, x)] as [number, number],
      }))
    : [];
  const nowBest = rows[minute]?.best ?? 0;

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
          </div>
        }
      />
      {!result ? (
        <Pending label={status === 'no-network' ? '길 데이터 준비 중' : '계산 중…'} height={260} />
      ) : (
        <div className="h-[260px]" role="img" aria-label={`대기 인원 추이. 현재 운영안 최대 ${formatNumber(result.current.totals.peakQueueTotal)}명, 분산 운영안 최대 ${formatNumber(result.best.result.totals.peakQueueTotal)}명`}>
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={rows} margin={{ top: 12, right: 8, bottom: 0, left: 0 }}>
              <CartesianGrid vertical={false} stroke="var(--color-line)" />
              <XAxis
                dataKey="x"
                type="number"
                domain={[0, MAX_MINUTE]}
                ticks={[0, 15, 30, 45, 60]}
                tickFormatter={(v) => `+${v}분`}
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
                  if (Array.isArray(v)) return [`${formatNumber(Number(v[0]))}~${formatNumber(Number(v[1]))}명`, '분산안 범위'];
                  return [`${formatNumber(Number(v))}명`, name === 'current' ? '현재 운영안' : '분산 운영안'];
                }}
                labelFormatter={(x) => `종료 후 ${x}분`}
                contentStyle={{ borderRadius: 8, border: '1px solid var(--color-line)', fontSize: 12 }}
              />
              <Area dataKey="band" stroke="none" fill="var(--color-accent)" fillOpacity={0.08} isAnimationActive={false} activeDot={false} />
              <Area dataKey="best" stroke="var(--color-accent)" strokeWidth={2.5} fill="var(--color-accent)" fillOpacity={0.1} isAnimationActive={false} />
              <Line dataKey="current" stroke="#9AA29D" strokeWidth={2} strokeDasharray="5 5" dot={false} isAnimationActive={false} />
              <ReferenceLine x={minute} stroke="var(--color-ink)" strokeDasharray="3 3" />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}
      <div className="flex items-center justify-between text-[12px] leading-[18px] text-ink-2">
        <span>대기열은 통로별 유입·통과를 1분 단위로 계산한 결과입니다. 범위는 퇴장 인원 예측 범위 상·하단으로 다시 계산한 값입니다.</span>
        {result && (
          <span className="shrink-0 font-medium text-ink">
            종료 +{minute}분 · {formatNumber(nowBest)}명
          </span>
        )}
      </div>
    </Card>
  );
}
