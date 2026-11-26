import { Badge } from '../ui/Badge';
import { daysAhead, formatClock, formatDay, formatNumber, koreanDate } from '../../lib/format';
import { rankWords } from '../../model/brief';
import { useScenario } from '../../store/scenario';
import type { DataBundle, Evening, EventItem, Prediction } from '../../data/contract';

interface DayRow {
  date: string;
  events: EventItem[];
  evening: Evening | null;
}

/** 공연을 날짜별로 묶는다. 서버가 준 역 승차 예측이 있으면 그 날짜에 붙인다 */
function byDate(data: DataBundle): DayRow[] {
  const days = new Map<string, EventItem[]>();
  for (const e of data.events.events) {
    const date = koreanDate(e.startAt);
    days.set(date, [...(days.get(date) ?? []), e]);
  }
  return [...days.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, events]) => ({
      date,
      events: events.sort((a, b) => a.startAt.localeCompare(b.startAt)),
      evening: data.evenings?.find((x) => x.date === date) ?? null,
    }));
}

/** 그날 공연 하나. 누르면 지도와 브리핑이 이 공연으로 바뀐다. 역 승차 예측이 없는 작은 공연은 한 줄로 줄인다 */
function Session({ event, prediction, selected, onSelect }: { event: EventItem; prediction: Prediction | undefined; selected: boolean; onSelect: () => void }) {
  const riders = prediction?.rail?.people;
  const tone = selected ? 'border-accent bg-accent-tint' : 'border-line bg-card hover:border-accent-hover';
  if (riders == null)
    return (
      <button
        type="button"
        aria-pressed={selected}
        onClick={onSelect}
        className={`flex h-[30px] items-center gap-1.5 self-center rounded-lg border px-2.5 text-[12px] transition-colors ${tone}`}
      >
        <span className={`font-medium ${selected ? 'text-accent' : 'text-ink'}`}>
          {event.venueName} {formatClock(event.startAt)}
        </span>
        {prediction?.attendance && <span className="text-ink-2">{formatNumber(prediction.attendance.value)}명</span>}
      </button>
    );
  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onSelect}
      className={`flex min-h-[46px] flex-col items-start justify-center rounded-lg border px-3 py-1.5 text-left transition-colors ${tone}`}
    >
      <span className={`text-[13px] leading-[18px] font-bold ${selected ? 'text-accent' : 'text-ink'}`}>
        {event.venueName} {formatClock(event.startAt)} <span className="font-normal text-ink-2">· {event.name}</span>
      </span>
      <span className="text-[12px] leading-[17px] text-ink-2">
        {prediction?.attendance ? `관객 ${formatNumber(prediction.attendance.value)}명 · ` : ''}지하철 {formatNumber(riders)}명
      </span>
    </button>
  );
}

/** 날짜별 공연 목록. 공연을 누르면 onPick으로 알린다 */
export function EveningList({ onPick }: { onPick?: (eventId: string) => void }) {
  const data = useScenario((s) => s.data);
  const eventId = useScenario((s) => s.eventId);
  const select = useScenario((s) => s.selectEvent);
  const choose = (id: string) => {
    select(id);
    onPick?.(id);
  };

  const rows = data ? byDate(data) : [];
  const today = data ? koreanDate(data.predictions.generatedAt) : '';
  const predictions = new Map(data?.predictions.predictions.map((p) => [p.eventId, p]));
  const largest = Math.max(1, ...rows.map((r) => r.evening?.window?.high ?? 0));
  const ranked = rows.find((r) => r.evening?.rank)?.evening?.rank;

  return (
    <div className="flex flex-col">
      {!data ? (
        <p className="px-6 pb-6 text-[13px] text-ink-2">공연 목록을 불러오는 중입니다…</p>
      ) : !rows.length ? (
        <p className="px-6 pb-6 text-[13px] text-ink-2">공원 달력에 올라온 공연이 없습니다.</p>
      ) : (
        <div role="table" aria-label="날짜별 공연과 역 승차 예측" className="text-[13px]">
          <div role="row" className="grid grid-cols-[100px_minmax(0,1fr)_250px_172px] gap-4 border-y border-line bg-subtle px-6 py-2.5 text-[12px] font-medium text-ink-2">
            <span role="columnheader">날짜</span>
            <span role="columnheader">공연</span>
            <span role="columnheader" className="flex items-center gap-1.5">
              올림픽공원역 퇴장 시간대 승차 <Badge kind="예측" />
            </span>
            <span role="columnheader">최근 1년 공연일 대비</span>
          </div>
          {rows.map((row) => {
            const window = row.evening?.window ?? null;
            const rank = row.evening?.rank ?? null;
            const active = row.events.some((e) => e.id === eventId);
            return (
              <div
                key={row.date}
                role="row"
                className={`grid grid-cols-[100px_minmax(0,1fr)_250px_172px] items-center gap-4 border-b border-line px-6 py-3 last:border-b-0 ${active ? 'bg-subtle' : ''}`}
              >
                <span role="cell" className="flex flex-col">
                  <span className="text-[14px] leading-5 font-bold text-ink">{formatDay(row.date)}</span>
                  <span className="text-[12px] leading-[17px] text-ink-2">{daysAhead(row.date, today)}</span>
                </span>
                <span role="cell" className="flex flex-wrap gap-2">
                  {row.events.map((e) => (
                    <Session key={e.id} event={e} prediction={predictions.get(e.id)} selected={e.id === eventId} onSelect={() => choose(e.id)} />
                  ))}
                </span>
                <span role="cell" className="flex flex-col gap-1.5">
                  {window ? (
                    <>
                      <span className="flex items-baseline gap-2">
                        <span className="text-[16px] leading-[22px] font-bold text-ink">{formatNumber(window.expected)}명</span>
                        <span className="text-[12px] text-ink-2" title="80% 예측 범위">
                          {formatNumber(window.low)}~{formatNumber(window.high)}명
                        </span>
                      </span>
                      {/* 막대 = 예측, 옅은 띠 = 80% 범위. 목록에서 가장 큰 범위의 위 끝이 오른쪽 끝이다 */}
                      <span className="relative h-1.5 rounded-full bg-page" aria-hidden="true">
                        <span
                          className="absolute inset-y-0 rounded-full bg-accent/25"
                          style={{ left: `${(window.low / largest) * 100}%`, width: `${((window.high - window.low) / largest) * 100}%` }}
                        />
                        <span className="absolute inset-y-0 left-0 rounded-full bg-accent" style={{ width: `${(window.expected / largest) * 100}%` }} />
                      </span>
                      {!!row.evening?.notIncluded && (
                        <span className="text-[12px] font-medium text-bottleneck">목록 외 공연장 행사 {row.evening.notIncluded}건 제외</span>
                      )}
                    </>
                  ) : (
                    <span className="text-ink-2">{row.evening ? '승차 증가가 예상되는 대형 공연 없음' : '역 승차 예측 없음'}</span>
                  )}
                </span>
                <span role="cell" className="flex flex-col">
                  {rank ? (
                    <>
                      <span className="text-[14px] leading-5 font-bold text-ink">{rankWords(rank.percentile)}</span>
                      <span className="text-[12px] leading-[17px] text-ink-2">
                        공연일 {formatNumber(rank.eventDays)}일 기준 · 백분위 {formatNumber(rank.percentile)}
                      </span>
                    </>
                  ) : (
                    <span className="text-ink-2">–</span>
                  )}
                </span>
              </div>
            );
          })}
        </div>
      )}
      {ranked && (
        <p className="border-t border-line px-6 py-3 text-[12px] leading-[18px] text-ink-2">
          승차 인원은 그날 모든 공연의 관객과 평소 승객을 더한 5·9호선 합계입니다. 비교 기준은 {formatDay(ranked.first)}~{formatDay(ranked.last)} 공연일{' '}
          {formatNumber(ranked.eventDays)}일의 실제 승차입니다. 중앙값은 {formatNumber(ranked.median)}명입니다.
        </p>
      )}
    </div>
  );
}
