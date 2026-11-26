import { Icon } from '../ui/Icon';
import { koreanDate } from '../../lib/format';
import { useScenario } from '../../store/scenario';

// 이전 서버는 모델 버전만 반환한다. 같은 버전의 배포 명세가 있으면 학습 완료일을 사용한다.
const manifests = import.meta.glob<string>('../../../ml/artifacts/egress-response/*/manifest.json', {
  eager: true,
  import: 'fitted_at',
  base: '../../../ml/artifacts/egress-response',
});

export function DataSourceNote() {
  const data = useScenario((s) => s.data);
  if (!data) return null;

  const source = data.source;
  const model = data.predictions.model;
  const mock = source?.kind !== 'server';
  const stored = source?.calendar === 'stored';
  const fittedAt = mock ? undefined : model.fittedAt ?? manifests[`./${model.version}/manifest.json`];
  const date = fittedAt && koreanDate(fittedAt);
  return (
    <span
      className={`inline-flex min-h-8 flex-wrap items-center gap-2 rounded-full border px-3 py-1 text-[13px] ${mock || stored ? 'border-bottleneck/40 bg-bottleneck-tint' : 'border-line bg-card'}`}
      role="status"
      title={mock ? undefined : `${model.version} · 모델 학습 완료일`}
    >
      <Icon name={mock || stored ? 'info' : 'notice'} size={14} />
      {mock ? '시연용 데이터' : <>예측 모델 : <span className="font-medium">{date || '생성일 미상'}</span></>}
      {stored && <span>· 저장된 공연 목록</span>}
      {!!source?.leftOut && <span>· 예측 제외 {source.leftOut}건</span>}
    </span>
  );
}
