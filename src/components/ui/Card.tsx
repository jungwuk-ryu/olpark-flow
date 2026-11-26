import type { HTMLAttributes, ReactNode } from 'react';

export function Card({ className, children, ...rest }: HTMLAttributes<HTMLElement>) {
  return (
    <section
      className={['rounded-[var(--radius-card)] border border-line bg-card', className ?? ''].join(' ')}
      {...rest}
    >
      {children}
    </section>
  );
}

interface CardHeaderProps {
  title: string;
  description?: string;
  actions?: ReactNode;
  className?: string;
  id?: string;
}

export function CardHeader({ title, description, actions, className, id }: CardHeaderProps) {
  return (
    <header className={['flex justify-between gap-4', description ? 'items-start' : 'items-center', className ?? ''].join(' ')}>
      <div className="min-w-0">
        <h2 id={id} className="text-[18px] leading-[26px] font-bold text-ink">
          {title}
        </h2>
        {description && <p className="mt-0.5 text-[13px] leading-5 text-ink-2">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </header>
  );
}

/** 1단계 뼈대용: 다음 단계에서 엔진 결과로 채울 자리. */
export function Pending({ label, height = 160 }: { label: string; height?: number }) {
  return (
    <div
      className="flex items-center justify-center rounded-[10px] border border-dashed border-line-strong bg-subtle text-[13px] text-ink-2"
      style={{ height }}
    >
      {label}
    </div>
  );
}
