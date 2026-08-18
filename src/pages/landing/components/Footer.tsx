import { Link } from 'react-router-dom'
import { scrollToSection } from '../../../lib/scrollToSection'

type FooterLink =
  | { label: string; to: string }
  | { label: string; section: string }

const LINK_CLASS = 'text-sm text-cream/70 hover:text-cream transition-colors duration-100'

const COL1: FooterLink[] = [
  { label: 'See demo', to: '/dashboard' },
  { label: 'How it works', section: 'how-it-works' },
  { label: 'How it grows', section: 'how-it-grows' },
]

const COL2: FooterLink[] = [
  { label: 'High-value zones', section: 'coverage' },
  { label: 'Checkout & exits', section: 'coverage' },
  { label: 'Book a pilot', section: 'contact' },
]

function FooterLinkItem({ item }: { item: FooterLink }) {
  if ('to' in item) {
    return (
      <Link to={item.to} className={LINK_CLASS}>
        {item.label}
      </Link>
    )
  }
  return (
    <a href={`#${item.section}`} onClick={scrollToSection(item.section)} className={LINK_CLASS}>
      {item.label}
    </a>
  )
}

const COL3_TEXT = [
  'Runs fully on-prem.',
  'Video never leaves your building.',
  'No cloud subscription.',
  'Pilot tuned to your cameras.',
]

export default function Footer() {
  return (
    <footer className="bg-ink text-cream/70 px-6 py-12">
      <div className="mx-auto max-w-[1200px]">
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-10 mb-12">
          {/* Col 1 — Product */}
          <div>
            <p className="text-[11px] font-bold uppercase tracking-wider text-cream/40 mb-4">Product</p>
            <ul className="flex flex-col gap-2.5">
              {COL1.map((item) => (
                <li key={item.label}>
                  <FooterLinkItem item={item} />
                </li>
              ))}
            </ul>
          </div>

          {/* Col 2 — Markets */}
          <div>
            <p className="text-[11px] font-bold uppercase tracking-wider text-cream/40 mb-4">Markets</p>
            <ul className="flex flex-col gap-2.5">
              {COL2.map((item) => (
                <li key={item.label}>
                  <FooterLinkItem item={item} />
                </li>
              ))}
            </ul>
          </div>

          {/* Col 3 — Privacy + honesty notes */}
          <div>
            <p className="text-[11px] font-bold uppercase tracking-wider text-cream/40 mb-4">Privacy</p>
            <ul className="flex flex-col gap-2">
              {COL3_TEXT.map((t) => (
                <li key={t} className="text-sm leading-snug">{t}</li>
              ))}
            </ul>
          </div>
        </div>

        {/* Bottom bar */}
        <div className="pt-6 border-t border-cream/10 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <span className="h-5 w-5 rounded bg-teal-900 flex items-center justify-center">
              <span className="h-2 w-2 rounded-full bg-teal-300" />
            </span>
            <span className="text-xs text-cream/50">Ketch Enterprise AI — CV Loss Prevention</span>
          </div>
          <p className="text-xs text-cream/30">
            © 2026 Ketch Enterprise AI · Computer-vision loss prevention
          </p>
        </div>
      </div>
    </footer>
  )
}
