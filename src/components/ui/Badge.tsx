import { Icon } from './Icon';

/** 값 출처 배지. 예측=엔진 계산, 입력=운영자 값, 가정=설정 파일 가정값, 관측=연동 실측. */
export type BadgeKind = '예측' | '입력' | '가정' | '관측' | 'AI 예측' | '직접 입력' | '시연용';

const STYLE: Record<BadgeKind, string> = {
  예측: 'bg-accent-tint text-accent',
  입력: 'bg-src-input-tint text-src-input',
  가정: 'bg-src-assume-tint text-src-assume',
  관측: 'bg-src-observed-tint text-src-observed',
  'AI 예측': 'bg-accent-tint text-accent',
  '직접 입력': 'bg-src-input-tint text-src-input',
  시연용: 'bg-src-assume-tint text-src-assume',
};

const TITLE: Record<BadgeKind, string> = {
  예측: '계산 엔진이 만든 값',
  입력: '운영자가 정한 값',
  가정: '설정 파일의 가정값',
  관측: '연동 데이터 실측값',
  'AI 예측': 'AI 예측 결과를 그대로 쓰는 중',
  '직접 입력': '운영자가 AI 예측값을 바꿈',
  시연용: '시연용 목업 값',
};

export function Badge({ kind }: { kind: BadgeKind }) {
  return (
    <span
      title={TITLE[kind]}
      className={`inline-flex h-5 items-center gap-[3px] rounded px-1.5 text-[11px] leading-4 font-medium whitespace-nowrap ${STYLE[kind]}`}
    >
      {kind === 'AI 예측' && <Icon name="sparkles" size={11} />}
      {kind}
    </span>
  );
}
