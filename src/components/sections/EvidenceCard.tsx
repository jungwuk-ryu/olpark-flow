import { Card, CardHeader } from '../ui/Card';
import { formatDay, formatMonths, formatNumber } from '../../lib/format';
import { selectPrediction, useScenario } from '../../store/scenario';

/** 이 예측을 얼마나 믿을 수 있나: 전날 예측을 실제 승차와 맞춰 본 기록과 유사 공연 */
export function EvidenceCard() {
  const data = useScenario((s) => s.data);
  const prediction = useScenario(selectPrediction);
  const track = data?.evidence?.track ?? null;
  const similar = prediction?.similarEvents ?? [];
  const scored = similar.filter((s) => s.stationForecast !== undefined && s.stationObserved !== undefined);

  return (
    <Card id="evidence" className="flex scroll-mt-[84px] flex-col gap-4 p-6 pt-5">
      <CardHeader title="신뢰도" description="공연 전날 승차 인원 예측과 실측 결과 비교" />
      {track ? (
        <>
          <dl className="grid grid-cols-3 gap-3">
            {[
              { label: '총량 오차', value: `${formatNumber(track.wapePct)}%`, note: `공연일 ${formatNumber(track.eventDays)}일` },
              {
                label: track.biasPct < 0 ? '과소 예측 일수 비율' : '과대 예측 일수 비율',
                value: `${formatNumber(track.biasPct < 0 ? track.underSharePct : 100 - track.underSharePct)}%`,
                note: `평균 ${formatNumber(Math.abs(track.biasPct))}% ${track.biasPct < 0 ? '낮게' : '높게'} 예측`,
              },
              ...(track.insideBandPct !== undefined ? [{ label: '예측 구간 적중률', value: `${formatNumber(track.insideBandPct)}%`, note: '80% 예측 구간 기준' }] : []),
            ].map((item) => (
              <div key={item.label} className="flex flex-col gap-0.5 rounded-[10px] border border-line px-3.5 py-3">
                <dt className="text-[12px] leading-[18px] text-ink-2">{item.label}</dt>
                <dd className="text-[22px] leading-[30px] font-bold text-ink">{item.value}</dd>
                <dd className="text-[12px] leading-[18px] text-ink-2">{item.note}</dd>
              </div>
            ))}
          </dl>
          <p className="text-[12px] leading-[18px] text-ink-2">
            {track.station} 퇴장 시간대 승차 인원 기준입니다. {formatMonths(track.first, track.last)} 각 월의 예측에는 전월까지의 자료를 사용했습니다. 이 기간은 모델 개발에도 사용됐습니다.
          </p>
        </>
      ) : (
        <p className="text-[13px] leading-5 text-ink-2">검증 자료가 없습니다.</p>
      )}

      <div className="flex flex-col gap-2">
        <h3 className="text-[15px] leading-[22px] font-bold">유사 공연</h3>
        {!similar.length ? (
          <p className="text-[13px] text-ink-2">유사 공연 자료가 없습니다.</p>
        ) : (
          <table className="w-full text-[13px]">
            <thead>
              <tr className="border-y border-line bg-subtle text-[12px] text-ink-2">
                <th scope="col" className="px-2 py-2 text-left font-medium">날짜</th>
                <th scope="col" className="px-2 py-2 text-left font-medium">공연</th>
                <th scope="col" className="px-2 py-2 text-right font-medium">발권 수</th>
                <th scope="col" className="px-2 py-2 text-right font-medium">역 승차 예측</th>
                <th scope="col" className="px-2 py-2 text-right font-medium">실제</th>
                <th scope="col" className="px-2 py-2 text-right font-medium">차이</th>
              </tr>
            </thead>
            <tbody>
              {similar.map((s) => {
                const has = s.stationForecast !== undefined && s.stationObserved !== undefined && s.stationObserved > 0;
                const gap = has ? (s.stationForecast! - s.stationObserved!) / s.stationObserved! : null;
                return (
                  <tr key={`${s.name}-${s.date}`} className="border-b border-line last:border-b-0">
                    <td className="px-2 py-2 whitespace-nowrap">{formatDay(s.date)} <span className="text-ink-2">{s.date.slice(0, 4)}</span></td>
                    <td className="px-2 py-2">{s.name}</td>
                    <td className="px-2 py-2 text-right">{formatNumber(s.attendance)}명</td>
                    <td className="px-2 py-2 text-right">{has ? `${formatNumber(s.stationForecast!)}명` : '–'}</td>
                    <td className="px-2 py-2 text-right font-bold">{has ? `${formatNumber(s.stationObserved!)}명` : '–'}</td>
                    <td className={`px-2 py-2 text-right font-medium ${gap !== null && Math.abs(gap) > 0.3 ? 'text-bottleneck' : 'text-ink-2'}`}>
                      {gap === null ? '–' : `${gap > 0 ? '+' : '−'}${formatNumber(Math.abs(gap) * 100)}%`}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        {scored.length > 0 && (
          <p className="text-[12px] leading-[18px] text-ink-2">
            동일 공연장의 출연자, 관객층, 개최일을 기준으로 선정했습니다. 승차 인원은 해당일 퇴장 시간대의 전체 승객이며, 차이는 실측 대비 예측 오차율입니다.
          </p>
        )}
      </div>
    </Card>
  );
}
