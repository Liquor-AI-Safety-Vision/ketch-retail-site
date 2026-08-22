import { Link } from 'react-router-dom'
import { Button } from '../../../components/ui'
import { CalendarCheck, MapPin, Wrench } from '@phosphor-icons/react'
import { scrollToSection } from '../../../lib/scrollToSection'

export default function PilotStrip() {
  return (
    <section className="bg-cream-200 border-y border-ink/8 px-6 py-16 md:py-20">
      <div className="mx-auto max-w-[1200px]">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-10 items-center">
          <div>
            <p className="mb-3 text-xs font-bold uppercase tracking-[0.18em] text-teal-600">Pilot offer</p>
            <h2 className="text-2xl md:text-3xl font-semibold tracking-tight leading-tight text-ink mb-4">
              One store. Two months. Tuned to your cameras.
            </h2>
            <p className="text-base leading-relaxed text-ink-600 max-w-[50ch]">
              We set up the system on-site, tune the zone boundaries to your floor plan, and run
              a two-month pilot. You see exactly how many times staff were flagged and how many
              checks led to an actual incident. Then you decide whether to expand.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <a href="#contact" onClick={scrollToSection('contact')}>
                <Button size="md">Start a pilot</Button>
              </a>
              <Link to="/dashboard">
                <Button size="md" variant="secondary">See demo first</Button>
              </Link>
            </div>
          </div>

          <div className="flex flex-col gap-4">
            <div className="flex items-start gap-4 bg-cream rounded-xl border border-ink/8 p-5">
              <CalendarCheck size={20} className="text-teal-600 mt-0.5 shrink-0" weight="duotone" />
              <div>
                <p className="text-sm font-semibold text-ink">Two-month pilot period</p>
                <p className="text-sm text-ink-600 leading-relaxed mt-1">
                  Enough time to see real patterns, build your label archive, and measure staff
                  review load — before any long-term commitment.
                </p>
              </div>
            </div>
            <div className="flex items-start gap-4 bg-cream rounded-xl border border-ink/8 p-5">
              <MapPin size={20} className="text-teal-600 mt-0.5 shrink-0" weight="duotone" />
              <div>
                <p className="text-sm font-semibold text-ink">One-location focus</p>
                <p className="text-sm text-ink-600 leading-relaxed mt-1">
                  We start with a single store so the model is trained on your specific lighting,
                  angles, and product placement. Results transfer cleanly to additional locations.
                </p>
              </div>
            </div>
            <div className="flex items-start gap-4 bg-cream rounded-xl border border-ink/8 p-5">
              <Wrench size={20} className="text-teal-600 mt-0.5 shrink-0" weight="duotone" />
              <div>
                <p className="text-sm font-semibold text-ink">One-time CapEx, not subscription</p>
                <p className="text-sm text-ink-600 leading-relaxed mt-1">
                  Hardware and setup billed once. Add-on modules such as SKU detection and behavior classification
                  are separate line items you buy when you're ready.
                </p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}
