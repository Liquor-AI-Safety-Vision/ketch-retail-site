import { useState, type MouseEvent } from 'react'
import { Link } from 'react-router-dom'
import { List, X } from '@phosphor-icons/react'
import { Button } from '../../../components/ui'
import ThemeToggle from '../../../components/ThemeToggle'
import { scrollToSection } from '../../../lib/scrollToSection'

export default function StickyNav() {
  const [open, setOpen] = useState(false)

  // Close the panel, then smooth-scroll. Panel is an absolute overlay so closing
  // it doesn't move the section offsets the scroll reads.
  const go = (id: string) => (e: MouseEvent<HTMLAnchorElement>) => {
    setOpen(false)
    scrollToSection(id)(e)
  }

  const aCls = 'block py-2.5 sm:py-0 whitespace-nowrap text-sm text-ink-600 hover:text-ink transition-colors no-underline'
  const anchors = (
    <>
      <a href="#cost" onClick={go('cost')} className={aCls}>The cost</a>
      <a href="#how-it-works" onClick={go('how-it-works')} className={aCls}>How it works</a>
      <a href="#how-it-grows" onClick={go('how-it-grows')} className={aCls}>How it grows</a>
      <a href="#coverage" onClick={go('coverage')} className={aCls}>Where it runs</a>
      <a href="#contact" onClick={go('contact')} className={aCls}>Contact</a>
    </>
  )

  return (
    <header className="sticky top-0 z-50 bg-cream/95 backdrop-blur border-b border-ink/8">
      <div className="mx-auto max-w-[1200px] px-6 h-14 flex items-center justify-between gap-3">
        {/* Brand */}
        <Link to="/" className="flex items-center gap-2.5 no-underline shrink-0">
          <span className="h-7 w-7 rounded-md bg-teal-900 flex items-center justify-center">
            <svg className="text-cream" width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
              <rect x="2" y="2" width="5" height="5" rx="1" fill="currentColor" opacity="0.9" />
              <rect x="9" y="2" width="5" height="5" rx="1" fill="currentColor" opacity="0.6" />
              <rect x="2" y="9" width="5" height="5" rx="1" fill="currentColor" opacity="0.6" />
              <rect x="9" y="9" width="5" height="5" rx="1" fill="currentColor" opacity="0.9" />
            </svg>
          </span>
          <span className="whitespace-nowrap text-sm font-semibold tracking-tight text-ink">Ketch Enterprise<span className="text-teal-600"> AI</span></span>
        </Link>

        {/* Section anchors — desktop only */}
        <nav className="hidden sm:flex items-center gap-6">{anchors}</nav>

        {/* Right controls — desktop */}
        <nav className="hidden sm:flex items-center gap-3">
          {/* EN/VI toggle — VI disabled until i18n lands on the retail site */}
          <div className="flex items-center rounded-md border border-ink/12 overflow-hidden text-xs font-semibold">
            <button className="px-2.5 py-1 bg-teal-900 text-cream">EN</button>
            <button
              disabled
              title="Tiếng Việt — coming soon"
              className="px-2.5 py-1 text-ink-600 opacity-50 cursor-not-allowed"
            >
              VI
            </button>
          </div>
          <ThemeToggle compact />
          <Link to="/dashboard">
            <Button size="sm" variant="secondary">See dashboard</Button>
          </Link>
          <a href="#contact" onClick={scrollToSection('contact')}>
            <Button size="sm">Book pilot</Button>
          </a>
        </nav>

        {/* Hamburger — mobile only */}
        <div className="sm:hidden flex items-center gap-2">
          <ThemeToggle compact />
          <button
            aria-label="Menu"
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
            className="flex h-9 w-9 items-center justify-center rounded-md border border-ink/12 text-ink hover:bg-ink/5 transition-colors"
          >
            {open ? <X size={18} weight="bold" /> : <List size={18} weight="bold" />}
          </button>
        </div>
      </div>

      {/* Mobile dropdown — absolute overlay (no page-width or layout shift). */}
      {open && (
        <div className="sm:hidden absolute inset-x-0 top-full border-t border-ink/10 bg-cream shadow-xl shadow-ink/10">
          <nav className="mx-auto max-w-[1200px] px-6 py-4 flex flex-col">
            <div className="flex flex-col gap-1 border-b border-ink/10 pb-3">{anchors}</div>
            <div className="mt-4 flex flex-col gap-2.5">
              <Link to="/dashboard" onClick={() => setOpen(false)}>
                <Button size="sm" variant="secondary" className="w-full">See dashboard</Button>
              </Link>
              <a href="#contact" onClick={go('contact')}>
                <Button size="sm" className="w-full">Book pilot</Button>
              </a>
            </div>
          </nav>
        </div>
      )}
    </header>
  )
}
