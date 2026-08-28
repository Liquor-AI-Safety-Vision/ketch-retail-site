import { Link } from 'react-router-dom'
import { scrollToSection } from '../../../lib/scrollToSection'

export default function CtaBand() {
  return (
    <section className="border-t border-band-border bg-band px-5 py-20 sm:px-8 md:py-24">
      <div data-reveal className="mx-auto grid max-w-[1280px] items-end gap-10 md:grid-cols-[1fr_auto]">
        <div>
        <p className="mb-5 text-sm font-semibold text-band-accent">
          One store / Two months / On-prem
        </p>
        <h2 className="max-w-[16ch] text-3xl font-semibold leading-[1.05] tracking-[-0.04em] text-band-text md:text-5xl">
          See the review workflow before discussing deployment.
        </h2>
        <p className="mt-6 max-w-[56ch] text-base leading-relaxed text-band-muted">
          Open the current dashboard, review the available camera and alert flows, then decide whether a store-specific pilot is worth the conversation.
        </p>
        </div>
        <div className="flex flex-wrap gap-3 md:justify-end">
          <Link to="/dashboard">
            <button className="rounded-md bg-band-text px-7 py-3.5 text-base font-semibold text-band
              hover:bg-white/85 transition-colors duration-150 focus-visible:outline-none
              focus-visible:ring-2 focus-visible:ring-band-accent focus-visible:ring-offset-2
              focus-visible:ring-offset-band active:translate-y-px whitespace-nowrap">
              View dashboard
            </button>
          </Link>
          <a href="#contact" onClick={scrollToSection('contact')}>
            <button className="rounded-md bg-transparent px-7 py-3.5 text-base font-semibold text-band-text
              border border-band-border hover:border-band-accent/60 hover:bg-white/5 transition-colors duration-150
              focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-band-accent
              focus-visible:ring-offset-2 focus-visible:ring-offset-band active:translate-y-px whitespace-nowrap">
              Book a pilot
            </button>
          </a>
        </div>
      </div>
    </section>
  )
}
