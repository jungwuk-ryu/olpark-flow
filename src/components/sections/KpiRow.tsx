import { Card } from '../ui/Card';
import { Badge } from '../ui/Badge';
import { Icon } from '../ui/Icon';
import { formatChange, formatNumber } from '../../lib/format';
import { deriveKpis } from '../../model/scenario';
import { useScenario } from '../../store/scenario';

interface Tile {
  label: string;
  value: string;
  unit: string;
  range?: string;
  rangeTitle?: string;
  note: string;
  positive: boolean;
}

export function KpiRow() {
  const result = useScenario((s) => s.result);
  const status = useScenario((s) => s.status);

  let tiles: Tile[];
  if (!result) {
    const empty = status === 'no-network' ? '길 데이터 준비 중' : '계산 중';
    tiles = ['예상 최대 대기 인원', '대기 + 추가 보행', '관객 1인당 추가 시간', '대기열 해소 예상'].map((label) => ({
      label,
      value: '–',
      unit: '',
      note: empty,
      positive: false,
    }));
  } else {
    const k = deriveKpis(result);
    const clearDiff = k.clear.value !== null && k.clear.current !== null ? k.clear.current - k.clear.value : null;
    tiles = [
      {
        label: '예상 최대 대기 인원',
        // 가장 붐빌 때 모든 병목에 줄 선 인원 합
        value: formatNumber(k.maxQueue.value ?? 0),
        unit: '명',
        range: `범위 ${formatNumber(k.maxQueueRange.low)}~${formatNumber(k.maxQueueRange.high)}명`,
        rangeTitle: `퇴장 인원 ${formatNumber(result.range.attendanceLow)}~${formatNumber(result.range.attendanceHigh)}명으로 다시 계산한 값`,
        note: `${formatChange(k.maxQueue.reduction)} · 현재 운영안 대비`,
        positive: (k.maxQueue.reduction ?? 0) > 0,
      },
      {
        label: '대기 + 추가 보행',
        value: formatNumber(k.walkBurden.value ?? 0),
        unit: '사람·분',
        note: `${formatChange(k.walkBurden.reduction)} · 현재 운영안 대비`,
        positive: (k.walkBurden.reduction ?? 0) > 0,
      },
      {
        label: '관객 1인당 추가 시간',
        value: formatNumber(k.perPerson.value ?? 0, 1),
        unit: '분',
        note: `${formatChange(k.perPerson.reduction)} · 현재 ${formatNumber(k.perPerson.current ?? 0, 1)}분 대비`,
        positive: (k.perPerson.reduction ?? 0) > 0,
      },
      {
        label: '대기열 해소 예상',
        value: k.clear.value === null ? `${result.best.result.minutes}+` : formatNumber(k.clear.value),
        unit: '분 후',
        note:
          clearDiff === null
            ? '계산 범위 안에 해소되지 않음'
            : clearDiff > 0
              ? `${clearDiff}분 단축 · 공연 종료 기준`
              : clearDiff < 0
                ? `${-clearDiff}분 늦어짐 · 공연 종료 기준`
                : '현재 운영안과 같음 · 공연 종료 기준',
        positive: (clearDiff ?? 0) > 0,
      },
    ];
  }

  return (
    <Card className="grid grid-cols-4" aria-busy={status === 'computing'}>
      {tiles.map((t, i) => (
        <div key={t.label} className={`flex flex-col gap-2 px-6 py-5 ${i < 3 ? 'border-r border-line' : ''}`}>
          <span className="flex items-center gap-1.5 text-[13px] leading-5 font-medium text-ink-2">
            {t.label}
            <Badge kind="예측" />
          </span>
          <span className="flex items-baseline gap-1">
            <span className="text-[34px] leading-[44px] font-bold tracking-[-0.01em] text-ink">{t.value}</span>
            <span className="text-[14px] font-medium text-ink-2">{t.unit}</span>
          </span>
          {t.range && (
            <span className="flex items-center gap-1.5 text-[13px] leading-[18px] font-medium text-ink" title={t.rangeTitle}>
              {t.range}
              <span className="text-ink-2">
                <Icon name="info" size={14} />
              </span>
            </span>
          )}
          <span className={`text-[13px] leading-[18px] font-medium ${t.positive ? 'text-accent' : 'text-ink-2'}`}>{t.note}</span>
        </div>
      ))}
    </Card>
  );
}
