import type { ButtonHTMLAttributes } from 'react';
import { Icon, type IconName } from './Icon';

type Variant = 'primary' | 'secondary' | 'ghost';

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  icon?: IconName;
  block?: boolean;
}

const VARIANT: Record<Variant, string> = {
  primary:
    'bg-accent text-white hover:bg-accent-hover active:bg-accent-hover disabled:bg-line-strong disabled:text-white',
  secondary:
    'bg-card text-ink border border-line hover:border-line-strong hover:bg-subtle disabled:text-ink-2',
  ghost: 'text-accent hover:bg-accent-tint px-2 disabled:text-ink-2',
};

export function Button({ variant = 'secondary', icon, block, className, children, type = 'button', ...rest }: ButtonProps) {
  return (
    <button
      type={type}
      className={[
        'inline-flex h-10 items-center justify-center gap-2 rounded-[var(--radius-control)] px-4 text-[14px] font-medium whitespace-nowrap',
        'transition-colors duration-150 ease-out disabled:cursor-not-allowed',
        VARIANT[variant],
        block ? 'w-full' : '',
        className ?? '',
      ].join(' ')}
      {...rest}
    >
      {icon && <Icon name={icon} size={18} />}
      {children}
    </button>
  );
}
