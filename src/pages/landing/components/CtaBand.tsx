import { Link } from 'react-router-dom'
import { scrollToSection } from '../../../lib/scrollToSection'

export default function CtaBand() {
  return (
    <section className="bg-teal-900 px-6 py-20 md:py-24">
      <div className="mx-auto max-w-[1200px] text-center">
        <p className="mb-4 text-xs font-bold uppercase tracking-[0.18em] text-teal-300">
          One-store pilot · two months · on-prem
        </p>
        <h2 className="text-3xl md:text-4xl font-semibold tracking-tight leading-tight text-cream mb-6 max-w-2xl mx-auto">
          Stop loss before it leaves the store.
        </h2>
        <p className="text-base text-cream/70 max-w-[48ch] mx-auto mb-10 leading-relaxed">
          The dashboard is live. See two real events — the amber dwell, the red walkout — running
          on the demo. Then decide if it's worth a 30-minute call.
        </p>
        <div className="flex flex-wrap justify-center gap-4">
          <Link to="/dashboard">
            <button className="rounded-lg bg-cream text-teal-900 font-semibold text-base px-8 py-3.5
              hover:bg-cream-200 transition-colors duration-150 focus-visible:outline-none
              focus-visible:ring-2 focus-visible:ring-cream focus-visible:ring-offset-2
              focus-visible:ring-offset-teal-900 active:translate-y-px whitespace-nowrap">
              See it live
            </button>
          </Link>
          <a href="#contact" onClick={scrollToSection('contact')}>
            <button className="rounded-lg bg-transparent text-cream font-semibold text-base px-8 py-3.5
              border border-cream/30 hover:border-cream/60 hover:bg-cream/5 transition-colors duration-150
              focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cream
              focus-visible:ring-offset-2 focus-visible:ring-offset-teal-900 active:translate-y-px whitespace-nowrap">
              Book pilot
            </button>
          </a>
        </div>
      </div>
    </section>
  )
}
