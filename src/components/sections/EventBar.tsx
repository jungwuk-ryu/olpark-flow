import { Card } from '../ui/Card';
import { Badge } from '../ui/Badge';
import { Icon } from '../ui/Icon';
import { formatClockRange, formatEventEnd, formatNumber } from '../../lib/format';
import { clockTimes } from '../../engine';
import { selectEvent, useEndTime, useScenario } from '../../store/scenario';
import { endWindowText, venueHasNetwork } from '../../model/scenario';

const STATUS_TEXT = {
  loading: '데이터 불러오는 중',
  computing: '계산 중…',
  ready: '분산 운영안 계산 완료',
  dirty: '조건 변경됨 · 다시 계산 필요',
  'no-network': '이 공연장은 길 데이터 준비 중',
  'no-events': '게시된 공연 없음',
  error: '계산 오류',
} as const;

export function EventBar() {
  const data = useScenario((s) => s.data);
  const eventId = useScenario((s) => s.eventId);
  const status = useScenario((s) => s.status);
  const error = useScenario((s) => s.error);
  const event = useScenario(selectEvent);
  const choose = useScenario((s) => s.selectEvent);
  const end = useEndTime();

  const directions = data?.routes.routes.length ?? 0;
  const tone =
    status === 'ready' ? 'text-accent font-bold' : status === 'dirty' || status === 'error' ? 'text-bottleneck font-bold' : 'text-ink-2 font-medium';
  // 종료 시각은 전날에 확정되지 않는다. 범위가 있으면 함께 적는다
  const range = end?.window ? clockTimes(end.expectedAt, 0, end.window) : null;
  const rangeTitle =
    end?.window && range
      ? `종료 시각 80% 범위 ${formatClockRange(range.earliest, range.latest)}` +
        (end.sdMinutes !== null ? ` · 전날 예측 오차의 표준편차 ${formatNumber(end.sdMinutes)}분` : '')
      : undefined;

  return (
    <Card className="grid grid-cols-[minmax(0,1fr)_250px_200px_240px]">
      <label className="relative flex flex-col gap-1.5 border-r border-line px-5 py-4 focus-within:bg-subtle">
        <span className="text-[12px] leading-[18px] text-ink-2">분석 행사</span>
        <span className="flex items-center justify-between gap-2 text-[15px] leading-[22px] font-medium">
          <span className="truncate">{event ? `${event.venueName} · ${event.name}` : '—'}</span>
          <Icon name="chevron-down" className="text-ink-2" />
        </span>
        <select
          aria-label="분석 행사 선택"
          className="absolute inset-0 cursor-pointer opacity-0"
          value={eventId ?? ''}
          onChange={(e) => choose(e.target.value)}
          disabled={!data || !data.events.events.length}
        >
          {data?.events.events.map((e) => (
            <option key={e.id} value={e.id}>
              {e.venueName} · {e.name} ({formatEventEnd(e.endAt)}){venueHasNetwork(data, e.venueId) ? '' : ' — 길 데이터 준비 중'}
            </option>
          ))}
        </select>
      </label>
      <div className="flex flex-col gap-1.5 border-r border-line px-5 py-4">
        <span className="flex items-center gap-1.5 text-[12px] leading-[18px] text-ink-2">
          {end?.entered ? '종료 시각' : '종료 예상'}
          {end?.entered ? <Badge kind="입력" /> : end?.window && <Badge kind="예측" />}
        </span>
        <span className="flex items-baseline gap-2 text-[15px] leading-[22px] font-medium" title={rangeTitle}>
          {end ? formatEventEnd(end.expectedAt) : '—'}
          {end?.window && <span className="text-[13px] text-ink-2">{endWindowText(end.window)}</span>}
        </span>
      </div>
      <div className="flex flex-col gap-1.5 border-r border-line px-5 py-4">
        <span className="text-[12px] leading-[18px] text-ink-2">기준 보행 경로</span>
        <span className="flex items-center gap-2 text-[15px] leading-[22px] font-medium">
          <span className="flex gap-[3px]" aria-hidden="true">
            <span className="size-2 rounded-full bg-route-a" />
            <span className="size-2 rounded-full bg-route-b" />
            <span className="size-2 rounded-full bg-route-c" />
          </span>
          {directions}개 방면
        </span>
      </div>
      <div className="flex flex-col gap-1.5 px-5 py-4" aria-live="polite">
        <span className="text-[12px] leading-[18px] text-ink-2">분석 상태</span>
        <span className={`text-[15px] leading-[22px] ${tone}`} title={error ?? undefined}>
          {STATUS_TEXT[status]}
        </span>
      </div>
    </Card>
  );
}
