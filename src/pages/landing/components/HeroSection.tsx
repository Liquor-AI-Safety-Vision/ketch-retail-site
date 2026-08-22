import { Link } from 'react-router-dom'
import { Button } from '../../../components/ui'
import { TierBadge } from '../../../components/ui'
import { scrollToSection } from '../../../lib/scrollToSection'

export default function HeroSection() {
  return (
    <section className="bg-cream min-h-[100dvh] flex items-center px-6 py-20">
      <div className="mx-auto max-w-[1200px] w-full grid grid-cols-1 md:grid-cols-[1fr_1fr] gap-12 md:gap-16 items-center">

        {/* Left — headline + copy + CTAs */}
        <div className="max-w-xl">
          <p className="mb-4 text-xs font-bold uppercase tracking-[0.18em] text-teal-600">
            Loss prevention · computer vision
          </p>
          <h1 className="text-4xl md:text-5xl lg:text-6xl font-semibold tracking-tight leading-[1.05] text-ink">
            Stop loss before it leaves the store
          </h1>
          <p className="mt-6 text-lg leading-relaxed text-ink-600 max-w-[52ch]">
            A triage system that flags when someone lingers at a high-value shelf and then moves
            toward the exit — then puts the relevant clip in front of your team for a fast,
            informed decision.
          </p>

          {/* Arc proof-points */}
          <div className="mt-8 flex flex-col gap-2.5">
            <div className="flex items-center gap-3 text-sm text-ink-600">
              <TierBadge tier={2} variant="soft" />
              <span>Dwell detected at premium-spirits shelf</span>
            </div>
            <div className="flex items-center gap-2 pl-4">
              <span className="h-4 w-px bg-ink/20" />
            </div>
            <div className="flex items-center gap-3 text-sm text-ink-600">
              <TierBadge tier={1} variant="soft" />
              <span>Subject now moving to exit — staff alerted</span>
            </div>
          </div>

          <div className="mt-10 flex flex-wrap gap-3">
            <Link to="/dashboard">
              <Button size="lg">See it live</Button>
            </Link>
            <a href="#contact" onClick={scrollToSection('contact')}>
              <Button size="lg" variant="secondary">Book pilot</Button>
            </a>
          </div>

          <p className="mt-5 text-xs text-ink-600/70 max-w-[44ch]">
            Tuned to your store's cameras during the pilot.
          </p>
        </div>

        {/* Right — framed live-feed teaser card */}
        <div className="relative">
          <div className="relative rounded-2xl overflow-hidden border border-ink/12 shadow-[0_4px_32px_-8px_rgba(21,33,31,0.18)] bg-ink aspect-video">
            {/* Current dashboard alert frame */}
            <img
              src="/demo-source/alerts/shoplifting/shoplifting-review.jpg"
              alt="Whole-store camera alert with a tracked customer"
              className="absolute inset-0 h-full w-full object-cover"
            />

            {/* Recording dot + label */}
            <div className="absolute top-3 left-3 flex items-center gap-1.5 bg-ink/60 rounded-md px-2.5 py-1 text-cream text-[11px] font-semibold tracking-wide">
              <span className="h-2 w-2 rounded-full bg-red motion-safe:animate-pulse" />
              ALERT · CAM 09 · Whole store
            </div>
          </div>

          {/* Floating "how it knows" callout */}
          <div className="absolute -bottom-4 -left-4 max-w-[220px] rounded-xl bg-cream-200 border border-ink/10 shadow-[0_2px_12px_rgba(21,33,31,0.10)] px-3.5 py-3">
            <p className="text-[10px] font-bold uppercase tracking-wider text-teal-600 mb-1">Video-backed review</p>
            <p className="text-xs text-ink leading-snug">
              The tracked event and its surrounding footage arrive together for owner review.
            </p>
          </div>

          {/* Floating stat chip */}
          <div className="absolute -top-4 -right-4 rounded-xl bg-teal-900 border border-teal-600/20 shadow-sm px-3.5 py-2.5">
            <p className="text-[10px] font-bold uppercase tracking-wider text-teal-300 mb-0.5">On-prem</p>
            <p className="text-sm font-semibold text-cream">Zero cloud</p>
          </div>
        </div>
      </div>
    </section>
  )
}
