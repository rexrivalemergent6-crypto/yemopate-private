import React, { useEffect, useState } from 'react';
import { ArrowRight, BookOpen, Radio, Workflow, Receipt, ShieldCheck, HelpCircle, Repeat } from 'lucide-react';
import Logo from './Logo';
import CaChip from './CaChip';

export const X_URL = 'https://x.com/usenullcash';

export const XGlyph = ({ s = 15 }) => (
  <svg viewBox="0 0 24 24" width={s} height={s} fill="currentColor" aria-hidden="true"><path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z" /></svg>
);

const ITEMS = [
  { l: 'Now live: open the app', h: '/app', I: Radio, id: 'live' },
  { l: 'Docs', h: '/docs', I: BookOpen, id: 'docs' },
  { l: 'Meet the community', h: X_URL, I: null, id: 'x', ext: true },
  { l: 'How it works', h: '/#flow', I: Workflow, id: 'flow' },
  { l: 'Swap without a wallet', h: '/app#private-route', I: Repeat, id: 'swap' },
  { l: 'Fees', h: '/docs#fees', I: Receipt, id: 'fees' },
  { l: 'Privacy model', h: '/docs#visibility', I: ShieldCheck, id: 'privacy' },
  { l: 'FAQ', h: '/#faq', I: HelpCircle, id: 'faq' },
];

export default function NmNav() {
  const [dark, setDark] = useState(false);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const f = () => {
      const secs = Array.from(document.querySelectorAll('[data-nm-theme]'));
      const hit = secs.find((s) => { const r = s.getBoundingClientRect(); return r.top <= 50 && r.bottom > 50; });
      setDark(hit ? hit.dataset.nmTheme === 'dark' : false);
    };
    f(); window.addEventListener('scroll', f, { passive: true });
    return () => window.removeEventListener('scroll', f);
  }, []);
  useEffect(() => {
    if (!open) return undefined;
    const k = (e) => { if (e.key === 'Escape') setOpen(false); };
    window.addEventListener('keydown', k);
    return () => window.removeEventListener('keydown', k);
  }, [open]);
  const d = dark && !open;
  const line = d ? 'border-[#D9D9D9]/40' : 'border-[#202221]/70';
  return (
    <header className="fixed inset-x-0 top-0 z-50 px-3 pt-3 sm:px-6">
      <div className={`mx-auto max-w-[1180px] overflow-hidden rounded-[14px] border backdrop-blur-md transition-colors duration-500 ${line} ${d ? 'bg-[#202221]/80 text-[#D9D9D9]' : 'bg-[#D9D9D9]/90 text-[#202221]'}`}>
        <div className="flex h-[64px] items-center justify-between gap-3 px-3 sm:px-4">
          <a href="/" data-testid="nav-logo" className="shrink-0"><Logo light={d} /></a>
          <div className="flex min-w-0 items-center gap-2">
            <CaChip line={line} />
            <a href={X_URL} target="_blank" rel="noopener noreferrer" aria-label="NullCash on X" data-testid="nav-x"
              className={`hidden h-10 w-10 place-items-center rounded-[10px] border transition hover:-translate-y-0.5 sm:grid ${line}`}><XGlyph /></a>
            <button type="button" onClick={() => setOpen((v) => !v)} aria-label={open ? 'Close menu' : 'Open menu'} aria-expanded={open} data-testid="nav-menu"
              className={`nm-burger relative grid h-10 w-10 place-items-center rounded-[10px] border ${line}`}>
              <span className={`nm-bl ${open ? 'o1' : ''}`} /><span className={`nm-bl ${open ? 'o2' : ''}`} /><span className={`nm-bl ${open ? 'o3' : ''}`} />
            </button>
          </div>
        </div>
        <div className={`grid transition-all duration-500 ease-[cubic-bezier(.2,.9,.25,1)] ${open ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]'}`}>
          <nav className="overflow-hidden" data-testid="nav-drawer" aria-hidden={!open}>
            {ITEMS.map(({ l, h, I, id, ext }, i) => (
              <a key={id} href={h} onClick={() => setOpen(false)} tabIndex={open ? 0 : -1} data-testid={`nav-item-${id}`}
                {...(ext ? { target: '_blank', rel: 'noopener noreferrer' } : {})}
                style={{ transitionDelay: open ? `${i * 35}ms` : '0ms' }}
                className={`group flex items-center gap-3 border-t border-[#202221]/70 px-4 py-3.5 text-[16px] transition-all duration-300 hover:bg-[#202221] hover:text-[#B9C1CC] ${open ? 'translate-x-0 opacity-100' : '-translate-x-2 opacity-0'}`}>
                <span className="grid h-6 w-6 place-items-center">{I ? <I className="h-[19px] w-[19px]" strokeWidth={1.8} /> : <XGlyph s={17} />}</span>
                <span className="flex-1">{l}</span>
                <ArrowRight className="h-4 w-4 -translate-x-1 opacity-0 transition-all group-hover:translate-x-0 group-hover:opacity-100" />
              </a>
            ))}
            <div className="border-t border-[#202221]/70 p-3">
              <a href="/app" data-testid="nav-launch" className="nm-lime flex h-12 items-center justify-center gap-2 rounded-[10px] text-[15px] font-semibold text-[#202221]">Open app <ArrowRight className="h-4 w-4" /></a>
            </div>
          </nav>
        </div>
      </div>
    </header>
  );
}
