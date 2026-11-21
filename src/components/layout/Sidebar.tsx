import { useEffect, useState } from 'react';
import { Icon, type IconName } from '../ui/Icon';

export interface NavSection {
  id: string;
  label: string;
  icon: IconName;
}

export const NAV_SECTIONS: NavSection[] = [
  { id: 'overview', label: '운영 개요', icon: 'chart' },
  { id: 'flow-map', label: '흐름 지도', icon: 'map' },
  { id: 'dispersal', label: '분산 운영안', icon: 'route' },
  { id: 'results', label: '분석 결과', icon: 'history' },
];

const PARK_GUIDE_URL = 'https://www.ksponco.or.kr/olympicpark/';

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

export function Sidebar() {
  const [active, setActive] = useActiveSection(ids);

  return (
    <aside className="sticky top-0 flex h-screen w-[240px] shrink-0 flex-col justify-between bg-sidebar px-4 py-6 text-sidebar-text">
      <div className="flex flex-col gap-7">
        <a href="#overview" className="flex items-center gap-3 rounded-lg px-2" aria-label="OLPARK FLOW 처음으로">
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

      <div className="flex flex-col gap-2 border-t border-sidebar-divider px-2 pt-5">
        <p className="text-[12px] leading-[18px] font-medium text-sidebar-muted">분석 범위</p>
        <p className="text-[17px] leading-6 font-bold text-white">올림픽공원</p>
        <p className="text-[13px] leading-5 text-sidebar-text">
          공연장 출구부터 인근 지하철 지상 출입구와 주차장 차량 출구까지의 귀가 흐름을 다룹니다.
        </p>
        <a
          href={PARK_GUIDE_URL}
          target="_blank"
          rel="noreferrer"
          className="mt-1 inline-flex items-center gap-1.5 self-start text-[13px] font-medium text-sidebar-text hover:text-white hover:underline"
        >
          <Icon name="external" size={14} />
          공식 공원 안내
        </a>
      </div>
    </aside>
  );
}
