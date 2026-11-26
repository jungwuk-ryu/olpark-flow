import { useEffect, useRef, useState } from 'react';
import { Icon, type IconName } from '../ui/Icon';

export interface NavSection {
  id: string;
  label: string;
  icon: IconName;
}

export const NAV_SECTIONS: NavSection[] = [
  { id: 'simulation', label: '보행 시뮬레이션', icon: 'map' },
  { id: 'brief', label: '공연 브리핑', icon: 'notice' },
  { id: 'actions', label: '실행표와 근거', icon: 'clipboard' },
];

const menuItem = 'flex h-10 w-full items-center gap-3 rounded-lg px-3 text-[14px] text-sidebar-text hover:bg-sidebar-selected hover:text-white';

/** 화면에 보이는 섹션을 따라 선택 항목을 바꾼다 (scroll spy). */
function useActiveSection(ids: string[]) {
  const [active, setActive] = useState(ids[0]);
  useEffect(() => {
    const els = ids.map((id) => document.getElementById(id)).filter((el): el is HTMLElement => !!el);
    if (!els.length || typeof IntersectionObserver === 'undefined') return;
    const visible = new Map<string, number>();
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) visible.set(e.target.id, e.isIntersecting ? e.intersectionRatio : 0);
        const top = ids.find((id) => (visible.get(id) ?? 0) > 0);
        if (top) setActive(top);
      },
      { rootMargin: '-80px 0px -55% 0px', threshold: [0, 0.01, 0.25] },
    );
    els.forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, [ids]);
  return [active, setActive] as const;
}

const ids = NAV_SECTIONS.map((s) => s.id);

export function Sidebar({ onLogout }: { onLogout: () => void }) {
  const [active, setActive] = useActiveSection(ids);
  const profile = useRef<HTMLDialogElement>(null);

  return (
    <aside className="sticky top-0 flex h-screen w-[240px] shrink-0 flex-col justify-between bg-sidebar px-4 py-6 text-sidebar-text">
      <div className="flex flex-col gap-7">
        <a href="#simulation" className="flex items-center gap-3 rounded-lg px-2" aria-label="OLPARK FLOW 처음으로">
          <svg width="36" height="36" viewBox="0 0 36 36" fill="none" aria-hidden="true">
            <rect width="36" height="36" rx="9" fill="#DCE6E2" />
            <path d="M11 25V16.5a7 7 0 0 1 14 0V25" stroke="#1E3B36" strokeWidth="2.4" strokeLinecap="round" />
            <path d="M18 25v-7" stroke="#2C6B5F" strokeWidth="2.4" strokeLinecap="round" />
          </svg>
          <span className="flex flex-col">
            <span className="text-[16px] leading-[22px] font-bold tracking-[0.02em] text-white">OLPARK FLOW</span>
            <span className="text-[12px] leading-[18px] text-sidebar-muted">운영 판단 플랫폼</span>
          </span>
        </a>

        <nav aria-label="워크스페이스">
          <p className="px-3 pb-2 text-[12px] leading-[18px] font-medium text-sidebar-muted">워크스페이스</p>
          <ul className="flex flex-col gap-1">
            {NAV_SECTIONS.map((s) => {
              const on = s.id === active;
              return (
                <li key={s.id}>
                  <a
                    href={`#${s.id}`}
                    onClick={() => setActive(s.id)}
                    aria-current={on ? 'location' : undefined}
                    className={[
                      'flex h-10 items-center gap-3 rounded-lg px-3 text-[14px] transition-colors duration-150',
                      on
                        ? 'bg-sidebar-selected font-bold text-white'
                        : 'text-sidebar-text hover:bg-sidebar-selected/60',
                    ].join(' ')}
                  >
                    <span className={on ? 'text-sidebar-text' : 'text-sidebar-muted'}>
                      <Icon name={s.icon} size={18} />
                    </span>
                    {s.label}
                  </a>
                </li>
              );
            })}
          </ul>
        </nav>
      </div>

      <nav aria-label="운영 메뉴" className="flex flex-col gap-1 border-t border-sidebar-divider pt-4">
        <button type="button" className={menuItem} aria-haspopup="dialog" onClick={() => profile.current?.showModal()}>
          <Icon name="user" size={18} />프로필
        </button>
        <a className={menuItem} href="#simulation-settings">
          <Icon name="sliders" size={18} />설정
        </a>
        <button type="button" className={menuItem} onClick={onLogout}>
          <Icon name="logout" size={18} />로그아웃
        </button>
      </nav>
      <dialog ref={profile} aria-labelledby="profile-heading" className="m-auto w-80 rounded-[var(--radius-card)] border border-line bg-card p-5 text-ink shadow-pop backdrop:bg-ink/45">
        <header className="flex items-center justify-between">
          <h2 id="profile-heading" className="text-lg font-bold">프로필</h2>
          <button type="button" onClick={() => profile.current?.close()} aria-label="닫기" className="grid size-8 place-items-center rounded-lg hover:bg-subtle">
            <Icon name="x" size={16} />
          </button>
        </header>
        <dl className="mt-4 grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 border-t border-line pt-4">
          <dt className="text-ink-2">역할</dt><dd>운영자</dd>
          <dt className="text-ink-2">워크스페이스</dt><dd>올림픽공원 운영센터</dd>
          <dt className="text-ink-2">접속 방식</dt><dd>로컬 세션</dd>
        </dl>
      </dialog>
    </aside>
  );
}
