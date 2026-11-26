import { Icon, type IconName } from '../ui/Icon';
import { Slider } from '../ui/Slider';
import { StatusLabel } from '../ui/StatusLabel';
import { formatNumber } from '../../lib/format';
import type { WalkDestination, WalkEdge, WalkEdgeSeries } from '../../engine';
import { KIND_NAME, type ControlKind } from '../../model/walkplan';
import { useWalk } from '../../store/walk';

export const CONTROL_NAME: Record<ControlKind, string> = { block: '통행 통제', narrow: '장애물', staff: '안전요원' };
export const CONTROL_ICON: Record<ControlKind, IconName> = { block: 'ban', narrow: 'fence', staff: 'user' };
const ACTION: { kind: ControlKind; label: string }[] = [
  { kind: 'block', label: '통행 통제' },
  { kind: 'narrow', label: '장애물 설치' },
  { kind: 'staff', label: '안전요원 배치' },
];
export const WIDTH_SOURCE: Record<WalkEdge['widthSource'], string> = { 판독: '항공사진 판독', OSM: 'OSM 태그', 가정: '가정' };

/** 지도에서 고른 길의 선택 상자: 지금 상태를 보여 주고 막기·장애물·안전요원을 놓거나 치운다 */
export function EdgeChoice({
  edge,
  series,
  where,
  minuteIndex,
  capacityPerMin,
  stations,
  compliance,
}: {
  edge: WalkEdge;
  series: WalkEdgeSeries | undefined;
  where: string;
  /** null이면 저녁 전체, 숫자면 그 분 (0부터) */
  minuteIndex: number | null;
  capacityPerMin: number;
  stations: WalkDestination[];
  compliance: number;
}) {
  const controls = useWalk((s) => s.controls);
  const walk = useWalk.getState;
  const placed = controls.blocked.includes(edge.id) ? 'block' : controls.narrowed[edge.id] !== undefined ? 'narrow' : controls.staffed.some((s) => s.edgeId === edge.id) ? 'staff' : null;
  const staff = controls.staffed.find((s) => s.edgeId === edge.id);
  const width = controls.narrowed[edge.id] ?? edge.widthM;
  const place = (kind: ControlKind) => {
    if (kind === placed) return;
    if (kind === 'block') walk().block(edge.id);
    else if (kind === 'narrow') walk().narrow(edge.id, edge.widthM / 2);
    else walk().staff(edge.id);
  };

  const t = minuteIndex;
  const status = series ? (t === null ? series.worst : t >= 0 ? series.status[t]! : '여유') : null;

  return (
    <div className="flex flex-col gap-3 p-4 text-[13px]">
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 flex-col">
          <span className="truncate text-[14px] leading-5 font-bold text-ink">{where}</span>
          <span className="text-[12px] leading-[18px] text-ink-2">
            {KIND_NAME[edge.kind]} · 길이 {formatNumber(edge.lengthM)}m
          </span>
        </div>
        <button
          type="button"
          onClick={() => walk().select(null)}
          aria-label="닫기"
          className="-mt-1 -mr-1 flex size-8 shrink-0 items-center justify-center rounded-lg text-ink-2 hover:bg-subtle hover:text-ink"
        >
          <Icon name="x" size={16} />
        </button>
      </div>

      <div className="flex flex-col gap-1 rounded-lg bg-subtle px-3 py-2">
        <span className="flex items-center gap-2">
          {status ? <StatusLabel status={status} /> : <span className="text-ink-2">{placed === 'block' ? '통제 구간' : '통행량 없음'}</span>}
          {series &&
            (t === null ? (
              <span className="text-ink">
                누적 통행량 {formatNumber(series.people)}명 · 최대 대기 {formatNumber(series.peakQueue)}명
              </span>
            ) : (
              <span className="text-ink">
                통행량 {formatNumber(t >= 0 ? series.flow[t]! : 0)}명/분 · 대기 {formatNumber(t >= 0 ? series.queue[t]! : 0)}명
              </span>
            ))}
        </span>
        <span className="text-[12px] leading-[18px] text-ink-2">
          폭 {formatNumber(width, 1)}m ({placed === 'narrow' ? `장애물, 원래 ${formatNumber(edge.widthM, 1)}m` : WIDTH_SOURCE[edge.widthSource]}) · 통행 용량{' '}
          {placed === 'block' ? '0' : formatNumber(capacityPerMin)}명/분
        </span>
      </div>

      <div className="grid grid-cols-3 gap-1.5" role="group" aria-label="구간 운영 조치">
        {ACTION.map((a) => (
          <button
            key={a.kind}
            type="button"
            aria-pressed={placed === a.kind}
            onClick={() => place(a.kind)}
            className={`flex h-[52px] flex-col items-center justify-center gap-1 rounded-lg border text-[12px] leading-4 font-medium transition-colors ${
              placed === a.kind ? 'border-accent bg-accent text-white' : 'border-line bg-card text-ink hover:border-accent-hover hover:bg-accent-tint'
            }`}
          >
            <Icon name={CONTROL_ICON[a.kind]} size={16} />
            {a.label}
          </button>
        ))}
      </div>

      {placed === 'block' && <p className="leading-5 text-ink-2">통제 구간을 제외한 우회 경로를 적용합니다.</p>}
      {placed === 'narrow' && (
        <div className="flex flex-col gap-1.5">
          <Slider
            label="유효 폭"
            value={controls.narrowed[edge.id]!}
            min={0.5}
            max={Math.max(1, edge.widthM)}
            step={0.1}
            onChange={(v) => walk().narrow(edge.id, v)}
            valueText={`${formatNumber(controls.narrowed[edge.id]!, 1)}m`}
            minLabel="0.5m"
            maxLabel={`원래 ${formatNumber(edge.widthM, 1)}m`}
          />
          <p className="leading-5 text-ink-2">장애물 설치 후의 유효 폭을 기준으로 통행 용량을 산정합니다.</p>
        </div>
      )}
      {placed === 'staff' && (
        <div className="flex flex-col gap-1.5">
          <label className="flex items-center justify-between gap-2">
            <span className="shrink-0 text-ink-2">안내 내용</span>
            <select
              value={staff?.sendTo ?? ''}
              onChange={(e) => walk().staff(edge.id, e.target.value || undefined)}
              className="h-9 min-w-0 flex-1 rounded-[var(--radius-control)] border border-line bg-card px-2 text-[13px] text-ink"
            >
              <option value="">우회 안내</option>
              {stations.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name} 방면 안내
                </option>
              ))}
            </select>
          </label>
          <p className="leading-5 text-ink-2">
            안내 수용률 {formatNumber(compliance * 100)}% (가정) · 시뮬레이션 조건에서 조정
          </p>
        </div>
      )}
      {placed && (
        <button type="button" onClick={() => walk().remove(edge.id)} className="self-start text-[13px] font-medium text-ink-2 underline decoration-line-strong hover:text-ink">
          {CONTROL_NAME[placed]} 해제
        </button>
      )}
    </div>
  );
}
