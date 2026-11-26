export function PageHeader() {
  return (
    <div className="flex flex-col gap-1.5">
      <h1 className="text-[30px] leading-10 font-bold tracking-[-0.01em] text-ink">퇴장 흐름 예측 시스템</h1>
      <p className="text-[14px] leading-[22px] text-ink-2">공연 종료 후 보행로별 혼잡도를 예측하고, 통행 통제와 안전요원 배치에 따른 변화를 비교합니다.</p>
    </div>
  );
}
