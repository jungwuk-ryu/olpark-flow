import { Card, CardHeader } from '../ui/Card';
import { Button } from '../ui/Button';
import { Icon } from '../ui/Icon';
import { Slider } from '../ui/Slider';
import { Badge } from '../ui/Badge';
import { formatNumber } from '../../lib/format';
import { basisFacts, crowdOf, crowdRange, directionWalkMinutes, PARAM_LIMITS, summarySentence } from '../../model/scenario';
import { selectPrediction, useScenario } from '../../store/scenario';
import { DIRECTIONS, type Direction } from '../../engine/types';

export const ROUTE_COLOR: Record<Direction, string> = {
  A: 'var(--color-route-a)',
  B: 'var(--color-route-b)',
  C: 'var(--color-route-c)',
};

export function SettingsCard() {
  const data = useScenario((s) => s.data);
  const params = useScenario((s) => s.params);
  const result = useScenario((s) => s.result);
  const status = useScenario((s) => s.status);
  const prediction = useScenario(selectPrediction);
  const setParam = useScenario((s) => s.setParam);
  const recompute = useScenario((s) => s.recompute);

  const disabled = !params || status === 'no-network' || status === 'no-events' || status === 'loading';
  const L = PARAM_LIMITS;
  // 지하철 이용 비율: 차량 이용자를 뺀 인원보다 많을 수 없다
  const maxStationShare = params ? Math.max(L.stationShare.min, Math.floor((1 - params.vehicleRatio) * 100 + 1e-9) / 100) : 1;
  const crowd = params ? crowdOf(params) : null;
  const riders = prediction && params ? crowdRange(prediction, params) : null;
  const stationShare = crowd && crowd.attendance > 0 ? crowd.station / crowd.attendance : 0;
  const predictedShare = prediction?.rail?.ratio ?? null;
  const shareEdited = predictedShare !== null && params !== null && Math.abs(params.stationShare - predictedShare) > 1e-9;
  const routes = data?.routes.routes ?? [];
  // 가장 많이 가는 방면은 안내의 출발점이라 끌 수 없다
  const share = prediction?.directionShare ?? {};
  const fixed = DIRECTIONS.reduce<Direction>((a, b) => ((share[b] ?? 0) > (share[a] ?? 0) ? b : a), 'A');
  const walk = data ? directionWalkMinutes(data) : {};
  const basis = data && result ? basisFacts(data, result) : null;
  const toggleDirection = (d: Direction) => {
    if (!params || d === fixed) return;
    const on = params.allowedDirections.includes(d);
    setParam('allowedDirections', on ? params.allowedDirections.filter((x) => x !== d) : [...params.allowedDirections, d]);
  };

  return (
    <Card className="flex flex-col">
      <div className="flex flex-col gap-5 p-6 pt-5">
        <CardHeader
          title="분산 운영 설정"
          description="조건을 바꾸고 다시 계산하면 운영안을 새로 찾습니다"
          actions={
            <span className="text-ink-2">
              <Icon name="sliders" size={20} />
            </span>
          }
        />

        <Slider
          label="예상 퇴장 인원"
          value={params?.attendance ?? L.attendance.min}
          {...L.attendance}
          onChange={(v) => setParam('attendance', v)}
          valueText={`${formatNumber(params?.attendance ?? 0)}명`}
          minLabel={`${formatNumber(L.attendance.min)}명`}
          maxLabel={`${formatNumber(L.attendance.max)}명`}
          disabled={disabled}
        />
        <Slider
          label="지하철 이용 비율"
          badge={<Badge kind={predictedShare === null ? '가정' : shareEdited ? '직접 입력' : 'AI 예측'} />}
          value={Math.min(params?.stationShare ?? L.stationShare.min, maxStationShare)}
          min={L.stationShare.min}
          max={maxStationShare}
          step={L.stationShare.step}
          onChange={(v) => setParam('stationShare', v)}
          valueText={`${formatNumber(stationShare * 100)}% · ${formatNumber(crowd?.station ?? 0)}명`}
          minLabel={`${formatNumber(L.stationShare.min * 100)}%`}
          maxLabel={`${formatNumber(maxStationShare * 100)}% (차량 제외 전원)`}
          disabled={disabled}
        />
        {params && crowd && (
          <p className="-mt-3 text-[12px] leading-[18px] text-ink-2">
            역 방면 길에는 지하철을 타러 가는 관객만 넣습니다. 차량 {formatNumber(params.vehicleRatio * 100)}% · 버스·도보 귀가·픽업 등{' '}
            {formatNumber(Math.max(0, 1 - stationShare - params.vehicleRatio) * 100)}%는 길 계산에서 빠집니다.
            {riders && prediction?.rail && (
              <>
                {' '}
                지하철 이용 인원 80% 예측 범위 {formatNumber(riders.low.station)}~{formatNumber(riders.high.station)}명.
              </>
            )}
            {predictedShare === null && ' 이 공연은 지하철 이용 비율 예측이 없어 차량을 뺀 전원으로 시작합니다.'}
          </p>
        )}
        <Slider
          label="퇴장 분포 시간"
          value={params?.egressTotalMinutes ?? L.egressTotalMinutes.min}
          {...L.egressTotalMinutes}
          onChange={(v) => setParam('egressTotalMinutes', v)}
          valueText={`${params?.egressTotalMinutes ?? 0}분`}
          minLabel={`${L.egressTotalMinutes.min}분`}
          maxLabel={`${L.egressTotalMinutes.max}분`}
          disabled={disabled}
        />
        <div className="h-px bg-line" />
        <Slider
          label="안내 반응률"
          badge={<Badge kind="가정" />}
          value={params?.compliance ?? 0}
          {...L.compliance}
          onChange={(v) => setParam('compliance', v)}
          valueText={`${formatNumber((params?.compliance ?? 0) * 100)}%`}
          minLabel="아무도 변경하지 않음"
          maxLabel="모두 따름"
          disabled={disabled}
        />
        <Slider
          label="안내요원"
          value={params?.staff ?? L.staff.min}
          {...L.staff}
          onChange={(v) => setParam('staff', v)}
          valueText={`${params?.staff ?? 0}명`}
          minLabel={`${L.staff.min}명`}
          maxLabel={`${L.staff.max}명`}
          disabled={disabled}
        />
        {data && params && (
          <p className="-mt-3 text-[12px] leading-[18px] text-ink-2">
            분기점 {Math.floor(params.staff / data.assumptions.staffPerJunction)}곳까지 안내 · 분기점당 {data.assumptions.staffPerJunction}명 (가정) · 길 용량은 늘지 않음
          </p>
        )}

        <fieldset className="flex flex-col gap-2.5" disabled={disabled}>
          <legend className="mb-2.5 flex w-full items-center justify-between text-[13px] font-medium">
            운영 가능한 분산 경로
            <span className="text-[12px] font-normal text-ink-2">{fixed} 경로 고정</span>
          </legend>
          <div className="grid grid-cols-3 gap-2">
            {routes.map((r) => {
              const on = params?.allowedDirections.includes(r.direction) ?? true;
              const locked = r.direction === fixed;
              return (
                <button
                  key={r.direction}
                  type="button"
                  aria-pressed={on}
                  disabled={locked || disabled}
                  onClick={() => toggleDirection(r.direction)}
                  title={locked ? '가장 많이 가는 방면이라 끌 수 없습니다' : on ? '눌러서 안내 대상에서 빼기' : '눌러서 안내 대상에 넣기'}
                  className={[
                    'flex h-9 items-center justify-center gap-1.5 rounded-lg border text-[13px] font-bold transition-colors',
                    on ? 'border-accent bg-accent-tint text-accent' : 'border-line bg-card text-ink-2 line-through',
                    locked ? 'cursor-default' : 'hover:border-accent-hover',
                  ].join(' ')}
                >
                  <span className="size-2 rounded-full" style={{ background: ROUTE_COLOR[r.direction] }} aria-hidden="true" />
                  {r.direction} 경로
                </button>
              );
            })}
          </div>
        </fieldset>

        <div className="rounded-lg bg-accent-tint px-3.5 py-3 text-[13px] leading-5 text-ink" aria-live="polite">
          {status === 'dirty'
            ? '조건이 바뀌었습니다. 다시 계산하면 운영안과 결과가 새로 나옵니다.'
            : status === 'no-network'
              ? '이 공연장은 길 데이터가 아직 없어 계산할 수 없습니다.'
              : result
                ? summarySentence(result)
                : '계산 중입니다…'}
        </div>
        <Button variant="primary" icon="refresh" block onClick={recompute} disabled={disabled || status === 'computing'}>
          {status === 'computing' ? '계산 중…' : '분산 운영안 다시 계산'}
        </Button>
      </div>

      <div className="flex flex-col gap-2.5 border-t border-line p-6 pt-5">
        <div className="flex items-center justify-between">
          <h3 className="text-[15px] leading-[22px] font-bold">방면별 안내 비중</h3>
          <span className="text-[12px] text-ink-2">분산 운영안 기준</span>
        </div>
        {routes.map((r) => {
          const d = r.direction;
          const now = result?.best.result.totals.directionShareActual[d] ?? 0;
          const before = result?.current.totals.directionShareActual[d] ?? 0;
          const people = result?.best.result.totals.directionPeople[d] ?? 0;
          const from = result?.best.plan.guidance.moves[0]?.from;
          return (
            <div key={d} className={`flex flex-col gap-2 rounded-[10px] border px-3.5 py-3 ${from === d ? 'border-accent' : 'border-line'}`}>
              <div className="flex items-center justify-between">
                <span className="flex items-center gap-2 text-[14px] font-medium">
                  <span className="size-2 rounded-full" style={{ background: ROUTE_COLOR[d] }} aria-hidden="true" />
                  {r.name}
                </span>
                <span className="flex items-baseline gap-1.5">
                  <span className="text-[11px] text-ink-2">현재 {formatNumber(before * 100)}%</span>
                  <span className="text-[15px] font-bold">{formatNumber(now * 100)}%</span>
                </span>
              </div>
              <div className="h-1.5 rounded-full bg-page" aria-hidden="true">
                <div className="h-full rounded-full transition-[width] duration-300 ease-out" style={{ width: `${now * 100}%`, background: ROUTE_COLOR[d] }} />
              </div>
              <div className="flex justify-between text-[12px] text-ink-2">
                <span>{formatNumber(walk[d] ?? 0)}분 기본 보행</span>
                <span>{formatNumber(people)}명 배정</span>
              </div>
            </div>
          );
        })}
        {basis && (
          <p className="mt-1 flex gap-1.5 text-[12px] leading-[18px] text-ink-2">
            <Icon name="info" size={14} className="mt-0.5" />
            <span>
              현재 운영안 최대 병목 {basis.placeName} · 종료 후 {basis.minute}분 유입 {formatNumber(basis.inflow)}명/분 · 대기{' '}
              {formatNumber(basis.queue)}명 · 통과 용량 {formatNumber(basis.capacity)}명/분 ({basis.edgeType} {formatNumber(basis.perMeter, 0)}명/m·분 × 유효 폭{' '}
              {formatNumber(basis.widthM, 1)}m, 가정)
            </span>
          </p>
        )}
      </div>
    </Card>
  );
}
