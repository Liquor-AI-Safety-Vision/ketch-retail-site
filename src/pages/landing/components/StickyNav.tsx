import { useEffect, useState, type MouseEvent } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, List, X } from '@phosphor-icons/react'
import ThemeToggle from '../../../components/ThemeToggle'
import { scrollToSection } from '../../../lib/scrollToSection'

const NAV_ITEMS = [
  { id: 'cost', label: 'Business case' },
  { id: 'how-it-works', label: 'How it works' },
  { id: 'how-it-grows', label: 'System roadmap' },
  { id: 'contact', label: 'Contact' },
]

export default function StickyNav() {
  const [open, setOpen] = useState(false)
  const [activeSection, setActiveSection] = useState('')

  useEffect(() => {
    const close = () => setOpen(false)
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }

    window.addEventListener('resize', close)
    window.addEventListener('keydown', closeOnEscape)
    return () => {
      window.removeEventListener('resize', close)
      window.removeEventListener('keydown', closeOnEscape)
    }
  }, [])

  useEffect(() => {
    const sections = NAV_ITEMS
      .map((item) => document.getElementById(item.id))
      .filter((section): section is HTMLElement => section !== null)

    if (!sections.length || !('IntersectionObserver' in window)) return

    const visible = new Map<string, number>()
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) visible.set(entry.target.id, entry.intersectionRatio)
        else visible.delete(entry.target.id)
      })

      const next = [...visible.entries()].sort((a, b) => b[1] - a[1])[0]?.[0]
      if (next) setActiveSection(next)
    }, {
      rootMargin: '-18% 0px -62% 0px',
      threshold: [0.01, 0.25, 0.5],
    })

    sections.forEach((section) => observer.observe(section))
    return () => observer.disconnect()
  }, [])

  const go = (id: string) => (event: MouseEvent<HTMLAnchorElement>) => {
    setOpen(false)
    setActiveSection(id)
    scrollToSection(id)(event)
  }

  const links = NAV_ITEMS.map((item) => (
    <a
      key={item.id}
      href={`#${item.id}`}
      onClick={go(item.id)}
      aria-current={activeSection === item.id ? 'location' : undefined}
      className={`rounded-md px-3 py-2 text-sm no-underline transition-colors lg:px-0 lg:py-2 ${
        activeSection === item.id
          ? 'bg-ink/5 font-semibold text-ink lg:bg-transparent lg:text-teal-600'
          : 'text-ink-600 hover:bg-ink/5 hover:text-ink lg:hover:bg-transparent'
      }`}
    >
      {item.label}
    </a>
  ))

  return (
    <header className="sticky top-0 z-50 border-b border-ink/10 bg-nav backdrop-blur-xl">
      <div className="mx-auto flex h-16 max-w-[1280px] items-center justify-between gap-4 px-5 sm:px-8">
        <a
          href={import.meta.env.BASE_URL}
          className="flex shrink-0 items-center gap-3 no-underline"
          aria-label="Ketch Enterprise AI home"
        >
          <span className="grid h-8 w-8 grid-cols-2 gap-[3px] rounded-md bg-teal-900 p-[7px]" aria-hidden="true">
            <span className="rounded-[1px] bg-cream" />
            <span className="rounded-[1px] bg-cream/55" />
            <span className="rounded-[1px] bg-cream/55" />
            <span className="rounded-[1px] bg-cream" />
          </span>
          <span className="text-sm font-semibold tracking-[-0.02em] text-ink">
            Ketch Enterprise <span className="text-teal-600">AI</span>
          </span>
        </a>

        <nav className="hidden items-center gap-7 lg:flex" aria-label="Main navigation">{links}</nav>

        <div className="hidden items-center gap-2 sm:flex">
          <ThemeToggle compact />
          <Link
            to="/dashboard"
            className="inline-flex h-9 items-center gap-2 rounded-md border border-ink/15 px-3.5 text-sm font-semibold text-ink transition-colors hover:border-teal-600/50 hover:bg-ink/5"
          >
            View dashboard
            <ArrowRight size={14} weight="bold" />
          </Link>
        </div>

        <div className="flex items-center gap-2 sm:hidden">
          <ThemeToggle compact />
          <button
            type="button"
            aria-label={open ? 'Close navigation' : 'Open navigation'}
            aria-expanded={open}
            aria-controls="landing-mobile-nav"
            onClick={() => setOpen((value) => !value)}
            className="flex h-9 w-9 items-center justify-center rounded-md border border-ink/15 text-ink transition-colors hover:bg-ink/5"
          >
            {open ? <X size={18} weight="bold" /> : <List size={18} weight="bold" />}
          </button>
        </div>
      </div>

      {open && (
        <div className="absolute inset-x-0 top-full border-b border-ink/10 bg-nav shadow-[0_20px_40px_-28px_rgba(10,30,28,0.5)] backdrop-blur-xl sm:hidden">
          <nav id="landing-mobile-nav" className="flex flex-col gap-1 px-5 py-5" aria-label="Mobile navigation">
            {links}
            <Link
              to="/dashboard"
              onClick={() => setOpen(false)}
              className="mt-4 inline-flex items-center justify-between rounded-md bg-teal-900 px-4 py-3 text-sm font-semibold text-cream"
            >
              View dashboard
              <ArrowRight size={15} weight="bold" />
            </Link>
          </nav>
        </div>
      )}
    </header>
  )
}
