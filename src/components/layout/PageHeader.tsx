import { Button } from '../ui/Button';

export function PageHeader() {
  return (
    <div className="flex flex-wrap items-end justify-between gap-4">
      <div className="flex flex-col gap-1.5">
        <h1 className="text-[30px] leading-10 font-bold tracking-[-0.01em] text-ink">퇴장 흐름 예측</h1>
        <p className="text-[14px] leading-[22px] text-ink-2">
          공연장 운영 분석 · 공연 종료 후 예상 대기열을 계산하고, 실행 가능한 안내안을 비교합니다.
        </p>
      </div>
      <div className="flex items-center gap-2">
        <Button icon="play">자동 재생</Button>
        <Button icon="map">관객 안내 보기</Button>
        <Button variant="primary" icon="download">
          결과 내보내기
        </Button>
      </div>
    </div>
  );
}
