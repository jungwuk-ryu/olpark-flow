import { Card } from '../ui/Card';
import { Badge } from '../ui/Badge';
import { Icon } from '../ui/Icon';
import { formatChange, formatClock, formatClockRange, formatNumber } from '../../lib/format';
import { clockTimes } from '../../engine';
import { deriveKpis, type EndTime } from '../../model/scenario';
import { useEndTime, useScenario } from '../../store/scenario';

interface Tile {
  label: string;
  value: string;
  unit: string;
  /** 인원 예측 범위로 다시 계산한 값 */
  range?: string;
  rangeTitle?: string;
  /** 시계 시각과, 종료 시각 범위를 반영한 폭 */
  time?: string;
  timeTitle?: string;
  note: string;
  positive: boolean;
}

/** 종료 후 minute분의 시계 시각. 종료 시각 범위가 있으면 "21:17 (20:54~21:40)" */
function clockText(end: EndTime, minute: number): string {
  const t = clockTimes(end.expectedAt, minute, end.window ?? undefined);
  return end.window ? `${formatClock(t.at)} (${formatClockRange(t.earliest, t.latest)})` : formatClock(t.at);
}

export function KpiRow() {
  const result = useScenario((s) => s.result);
  const status = useScenario((s) => s.status);
  const end = useEndTime();

  let tiles: Tile[];
  if (!result) {
    const empty = status === 'no-network' ? '길 데이터 준비 중' : status === 'no-events' ? '게시된 공연 없음' : '계산 중';
    tiles = ['예상 최대 대기 인원', '대기 + 추가 보행', '지하철 이용 관객 1인당 추가 시간', '대기열 해소 예상'].map((label) => ({
      label,
      value: '–',
      unit: '',
      note: empty,
      positive: false,
    }));
  } else {
    const k = deriveKpis(result);
    const clearDiff = k.clear.value !== null && k.clear.current !== null ? k.clear.current - k.clear.value : null;
    const timeTitle = end?.window
      ? '괄호 안은 종료 시각이 80% 범위 안에서 움직일 때의 시각입니다. 종료 시각은 전날에 확정되지 않습니다.'
      : '공연 목록의 종료 시각에 끝난다고 본 시각입니다.';
    tiles = [
      {
        label: '예상 최대 대기 인원',
        // 가장 붐빌 때 모든 병목에 줄 선 인원 합
        value: formatNumber(k.maxQueue.value ?? 0),
        unit: '명',
        range: `범위 ${formatNumber(k.maxQueueRange.low)}~${formatNumber(k.maxQueueRange.high)}명`,
        rangeTitle: `지하철 이용 인원 ${formatNumber(result.range.stationLow)}~${formatNumber(result.range.stationHigh)}명(80% 예측 범위)으로 다시 계산한 값`,
        time: end && (k.maxQueue.value ?? 0) > 0 ? `정점 ${clockText(end, k.maxQueueMinute)}` : undefined,
        timeTitle,
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
        label: '지하철 이용 관객 1인당 추가 시간',
        value: formatNumber(k.perPerson.value ?? 0, 1),
        unit: '분',
        note: `${formatChange(k.perPerson.reduction)} · 현재 ${formatNumber(k.perPerson.current ?? 0, 1)}분 대비`,
        positive: (k.perPerson.reduction ?? 0) > 0,
      },
      {
        label: '대기열 해소 예상',
        value: k.clear.value === null ? `${result.best.result.minutes}+` : formatNumber(k.clear.value),
        unit: '분 후',
        time: end && k.clear.value !== null ? `${clockText(end, k.clear.value)}` : undefined,
        timeTitle,
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
          {t.time && (
            <span className="flex items-center gap-1.5 text-[13px] leading-[18px] font-medium text-ink" title={t.timeTitle}>
              {t.time}
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
