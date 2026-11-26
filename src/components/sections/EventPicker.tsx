import { useEffect, useRef } from 'react';
import { Icon } from '../ui/Icon';
import { EveningList } from './EveningList';

/** 공연을 고르는 창. 고르면 닫히고 지도가 그 공연으로 바뀐다 */
export function EventPicker({ open, onClose }: { open: boolean; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const el = dialog.current;
    if (!el) return;
    if (open && !el.open) el.showModal();
    if (!open && el.open) el.close();
  }, [open]);

  return (
    <dialog
      ref={dialog}
      onClose={onClose}
      // 창 바깥(어두운 곳)을 누르면 닫는다
      onClick={(e) => e.target === dialog.current && onClose()}
      aria-labelledby="event-picker-title"
      className="m-auto w-[min(1120px,calc(100vw-48px))] max-w-none rounded-[var(--radius-card)] border border-line bg-card p-0 text-ink shadow-pop backdrop:bg-ink/45"
    >
      <div className="flex max-h-[calc(100vh-96px)] flex-col">
        <div className="flex items-start justify-between gap-4 border-b border-line px-6 py-4">
          <div>
            <h2 id="event-picker-title" className="text-[18px] leading-[26px] font-bold">
              공연 선택
            </h2>
          </div>
          <button type="button" onClick={onClose} aria-label="닫기" className="flex size-9 items-center justify-center rounded-lg text-ink-2 hover:bg-subtle hover:text-ink">
            <Icon name="x" size={18} />
          </button>
        </div>
        <div className="overflow-y-auto">{open && <EveningList onPick={onClose} />}</div>
      </div>
    </dialog>
  );
}
