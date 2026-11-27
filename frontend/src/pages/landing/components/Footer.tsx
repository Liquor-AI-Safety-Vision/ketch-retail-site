import { Link } from 'react-router-dom'
import { scrollToSection } from '../../../lib/scrollToSection'

type FooterLink =
  | { label: string; to: string }
  | { label: string; section: string }

const LINK_CLASS = 'inline-flex min-h-11 items-center rounded-sm text-sm text-band-muted transition-colors duration-100 hover:text-band-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-band-accent sm:min-h-0'

const COL1: FooterLink[] = [
  { label: 'View dashboard', to: '/dashboard' },
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
  'Primary video processing is designed to run on-site.',
  'Review access is authenticated and scoped to store staff.',
  'Camera feeds are not streamed to this public site.',
  'Pilot tuned to your cameras.',
]

export default function Footer() {
  return (
    <footer className="bg-footer px-5 py-12 text-band-muted sm:px-8">
      <div className="mx-auto max-w-[1280px]">
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-10 mb-12">
          {/* Col 1: Product */}
          <div>
            <p className="text-[11px] font-bold uppercase tracking-wider text-band-faint mb-4">Product</p>
            <ul className="flex flex-col gap-2.5">
              {COL1.map((item) => (
                <li key={item.label}>
                  <FooterLinkItem item={item} />
                </li>
              ))}
            </ul>
          </div>

          {/* Col 2: Markets */}
          <div>
            <p className="text-[11px] font-bold uppercase tracking-wider text-band-faint mb-4">Markets</p>
            <ul className="flex flex-col gap-2.5">
              {COL2.map((item) => (
                <li key={item.label}>
                  <FooterLinkItem item={item} />
                </li>
              ))}
            </ul>
          </div>

          {/* Col 3: Privacy and honesty notes */}
          <div>
            <p className="text-[11px] font-bold uppercase tracking-wider text-band-faint mb-4">Privacy</p>
            <ul className="flex flex-col gap-2">
              {COL3_TEXT.map((t) => (
                <li key={t} className="text-sm leading-snug">{t}</li>
              ))}
            </ul>
          </div>
        </div>

        {/* Bottom bar */}
        <div className="pt-6 border-t border-band-border flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <span className="h-5 w-5 rounded bg-teal-600/80 flex items-center justify-center">
              <span className="h-2 w-2 rounded-full bg-band-accent" />
            </span>
            <span className="text-xs text-band-muted">Ketch Enterprise AI / CV Loss Prevention</span>
          </div>
          <p className="text-xs text-band-faint">
            © 2026 Ketch Enterprise AI · Computer-vision loss prevention
          </p>
        </div>
      </div>
    </footer>
  )
}
