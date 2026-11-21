import { useId, type ReactNode } from 'react';

interface SliderProps {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
  /** 오른쪽 위에 보일 값 문구 */
  valueText: string;
  minLabel: string;
  maxLabel: string;
  badge?: ReactNode;
  disabled?: boolean;
  tint?: string;
}

export function Slider({ label, value, min, max, step, onChange, valueText, minLabel, maxLabel, badge, disabled, tint }: SliderProps) {
  const id = useId();
  const fill = ((value - min) / (max - min)) * 100;
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center justify-between gap-2">
        <label htmlFor={id} className="flex items-center gap-1.5 text-[13px] leading-5 font-medium text-ink">
          {label}
          {badge}
        </label>
        <output htmlFor={id} className="text-[14px] leading-5 font-bold text-ink">
          {valueText}
        </output>
      </div>
      <input
        id={id}
        type="range"
        className="olp-range"
        min={min}
        max={max}
        step={step}
        value={value}
        disabled={disabled}
        aria-valuetext={valueText}
        onChange={(e) => onChange(Number(e.target.value))}
        style={{ ['--fill' as string]: `${fill}%`, ...(tint ? { ['--tint' as string]: tint } : {}) }}
      />
      <div className="flex justify-between text-[11px] leading-4 text-ink-2">
        <span>{minLabel}</span>
        <span>{maxLabel}</span>
      </div>
    </div>
  );
}
