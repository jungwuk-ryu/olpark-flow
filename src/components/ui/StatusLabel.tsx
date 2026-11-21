import { Icon, type IconName } from './Icon';

/** 상태는 항상 아이콘 + 라벨 + 색으로 표시한다 (색만으로 의미 전달 금지). */
export type Status = '여유' | '보통' | '병목' | '위험';

const STYLE: Record<Status, { icon: IconName; tint: string; fg: string }> = {
  여유: { icon: 'status-ok', tint: 'bg-ok-tint', fg: 'text-ok' },
  보통: { icon: 'status-normal', tint: 'bg-normal-tint', fg: 'text-[#9A7A1F]' },
  병목: { icon: 'status-bottleneck', tint: 'bg-bottleneck-tint', fg: 'text-bottleneck' },
  위험: { icon: 'status-danger', tint: 'bg-danger-tint', fg: 'text-danger' },
};

export function StatusLabel({ status }: { status: Status }) {
  const s = STYLE[status];
  return (
    <span className={`inline-flex h-[22px] items-center gap-1 rounded-md pr-2 pl-1.5 text-[12px] font-bold text-ink ${s.tint}`}>
      <span className={s.fg}>
        <Icon name={s.icon} size={14} />
      </span>
      {status}
    </span>
  );
}
