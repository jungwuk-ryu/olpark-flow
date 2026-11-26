import { formatDateKo } from '../../lib/format';

/** source: 서버 예측을 받았는지, 시연용 목업인지, 아직 불러오는 중인지 */
export function TopBar({ date, source }: { date: Date; source: 'server' | 'mock' | 'loading' }) {
  return (
    <header className="sticky top-0 z-20 flex h-[60px] items-center justify-between border-b border-line bg-page/95 px-8 backdrop-blur-[2px]">
      <nav aria-label="현재 위치" className="flex items-center gap-2 text-[13px]">
        <span className="text-ink-2">운영센터</span>
        <span className="text-line-strong" aria-hidden="true">
          /
        </span>
        <span className="font-medium text-ink" aria-current="page">
          퇴장 흐름 예측 시스템
        </span>
      </nav>
      <div className="flex items-center gap-4 text-[13px]">
        <span className="flex items-center gap-1.5 font-medium">
          <span className={`size-2 rounded-full ${source === 'server' ? 'bg-ok' : source === 'mock' ? 'bg-bottleneck' : 'bg-line-strong'}`} aria-hidden="true" />
          {source === 'server' ? '연결 양호' : source === 'mock' ? '시연용 데이터' : '불러오는 중'}
        </span>
        <span className="h-4 w-px bg-line" aria-hidden="true" />
        <time dateTime={date.toISOString().slice(0, 10)} className="text-ink-2">
          {formatDateKo(date)}
        </time>
        <span
          className="flex size-8 items-center justify-center rounded-full bg-accent-tint text-[11px] font-bold text-accent"
          title="운영자"
        >
          운영
        </span>
      </div>
    </header>
  );
}
