import { CalendarCheck, MapPin, Wrench } from '@phosphor-icons/react'
import { Link } from 'react-router-dom'
import { Button } from '../../../components/ui'
import { scrollToSection } from '../../../lib/scrollToSection'

const PILOT_DETAILS = [
  {
    Icon: CalendarCheck,
    title: 'Two-month observation period',
    body: 'Long enough to capture repeat patterns and measure the actual review load before any long-term decision.',
  },
  {
    Icon: MapPin,
    title: 'One-store deployment',
    body: 'Camera angles, lighting, access rules, and alert thresholds are tuned to the selected location first.',
  },
  {
    Icon: Wrench,
    title: 'Defined installation scope',
    body: 'Hardware, configuration, and optional model modules are discussed as explicit line items, not hidden behind vague packaging.',
  },
]

export default function PilotStrip() {
  return (
    <section className="border-y border-ink/10 bg-cream-200 px-5 py-20 sm:px-8 md:py-24">
      <div className="mx-auto grid max-w-[1280px] gap-12 lg:grid-cols-[minmax(0,0.78fr)_minmax(520px,1.22fr)] lg:gap-20">
        <div data-reveal>
          <h2 className="max-w-[24ch] text-[clamp(2rem,3.4vw,2.75rem)] font-semibold leading-[1.06] tracking-[-0.04em] text-ink">
            <span className="block">One store. Two months.</span>
            <span className="mt-1 block">Tuned to your cameras.</span>
          </h2>
          <p className="mt-6 max-w-[52ch] text-base leading-relaxed text-ink-600">
            We configure the system around one operating location, establish a measurable review workflow,
            and leave the owner with evidence, not a sales projection.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <a href="#contact" onClick={scrollToSection('contact')}><Button>Book a pilot</Button></a>
            <Link to="/dashboard"><Button variant="secondary">View dashboard</Button></Link>
          </div>
        </div>

        <div data-reveal-group className="border-y border-ink/12">
          {PILOT_DETAILS.map(({ Icon, title, body }, index) => (
            <div
              data-reveal-item
              key={title}
              className="grid grid-cols-[34px_1fr] gap-5 border-b border-ink/10 py-6 last:border-b-0 md:grid-cols-[40px_minmax(180px,0.7fr)_minmax(0,1fr)] md:gap-7"
            >
              <Icon size={22} weight="duotone" className="text-teal-600" aria-hidden="true" />
              <h3 className="text-base font-semibold tracking-[-0.02em] text-ink">
                <span className="mr-2 text-xs text-ink-600/45">0{index + 1}</span>{title}
              </h3>
              <p className="col-start-2 text-sm leading-relaxed text-ink-600 md:col-start-3">{body}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}
