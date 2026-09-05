import { useState } from 'react'
import { UserCircleMinus, HandGrabbing, ChartLineDown, ArrowRight } from '@phosphor-icons/react'
import { SectionShell, LinkButton } from '../../../components/ui'
import { scrollToSection } from '../../../lib/scrollToSection'
import {
  BENCHMARKS,
  DEFAULT_RECOVERY,
  retailEstimate,
  fmtUsd,
} from '../../../lib/roi'

/**
 * Cost → solution → ROI story for the liquor/convenience store landing.
 * The narrative: here's what theft quietly costs a store this size (cited
 * industry benchmarks), and a calculator the owner drives with their own
 * monthly sales. Honesty rule: the benchmarks are published industry averages,
 * the avoidable-share slider is an owner assumption, and the output is an
 * illustrative scenario — never a prediction or a promised return.
 */

const COST_CARDS = [
  {
    Icon: ChartLineDown,
    stat: fmtUsd(BENCHMARKS.retail.cstoreShrinkPerStoreUsd),
    label: 'merchandise shrink per store, annualized',
    note: 'Based on preliminary NACS 2025 data of $1,703 per store each month. An industry reference, not a forecast for your location.',
    source: 'NACS preliminary 2025 data',
    sourceHref: 'https://www.convenience.org/stay-current/news/2026/may/27/reduce%E2%80%AFback-office-blind-spots%E2%80%AFthrough-smarter-data-analysis',
  },
  {
    Icon: UserCircleMinus,
    stat: fmtUsd(BENCHMARKS.retail.avgEmployeeCaseUsd),
    label: 'average dishonest-employee case value',
    note: 'Reported across apprehended dishonest employees at 26 large retailers in the 2022 survey.',
    source: 'Jack L. Hayes Retail Theft Survey, 2022',
    sourceHref: 'https://hayesinternational.com/news/annual-retail-theft-survey/',
  },
  {
    Icon: HandGrabbing,
    stat: '6.6%',
    label: 'of reported U.S. robberies occurred at c-stores',
    note: 'This FBI location share describes reported robbery offenses nationally, not the risk at any individual store.',
    source: 'FBI Crime in the U.S., 2019',
    sourceHref: 'https://ucr.fbi.gov/crime-in-the-u.s/2019/crime-in-the-u.s.-2019/figures/robbery-locations.gif/view',
  },
]

export default function CostRoiSection() {
  // Owner-driven inputs. Defaults sized for a single mid-volume liquor/c-store.
  const [monthly, setMonthly] = useState(100_000)
  const [recovery, setRecovery] = useState(DEFAULT_RECOVERY)
  const est = retailEstimate(monthly, recovery)

  return (
    <SectionShell
      id="cost"
      tone="cream-200"
      eyebrow="The cost of doing nothing"
      title={(
        <>
          <span className="block lg:whitespace-nowrap">What theft quietly costs</span>
          <span className="block lg:whitespace-nowrap">a store like yours</span>
        </>
      )}
      titleClassName="max-w-none text-[clamp(2rem,3.25vw,2.75rem)]"
      intro="Loss can accumulate through routine shrink as well as isolated incidents. Published industry figures show the scale; the calculator below lets you test an avoidable share you choose yourself."
    >
      {/* Industry benchmark ledger */}
      <div data-reveal-group className="grid border-y border-ink/12 md:grid-cols-3">
        {COST_CARDS.map((c, index) => (
          <div
            data-reveal-item
            key={c.label}
            className={`flex min-h-[300px] flex-col py-7 md:px-8 md:py-9 ${index > 0 ? 'border-t border-ink/10 md:border-l md:border-t-0' : ''}`}
          >
            <div className="flex items-center justify-between">
              <c.Icon size={24} weight="duotone" className="text-red" aria-hidden="true" />
              <span aria-hidden="true" className="text-[10px] font-semibold tracking-[0.18em] text-ink-600/45">0{index + 1}</span>
            </div>
            <p className="mt-10 text-4xl font-semibold tracking-[-0.045em] text-ink">{c.stat}</p>
            <p className="mt-2 text-sm font-medium text-ink">{c.label}</p>
            <p className="mt-4 text-sm leading-relaxed text-ink-600">{c.note}</p>
            <a
              href={c.sourceHref}
              target="_blank"
              rel="noreferrer"
              className="mt-auto inline-flex min-h-11 items-center self-start rounded-sm pt-7 text-[10px] font-medium uppercase
                tracking-[0.08em] text-ink-600/80 underline decoration-ink/25 underline-offset-4 transition-colors hover:text-ink
                focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-600"
            >
              {c.source}
            </a>
          </div>
        ))}
      </div>

      {/* Illustrative calculator — see tests/roi-estimate.test.mjs for the math contract */}
      <div className="mt-10 grid grid-cols-1 gap-0 overflow-hidden rounded-lg border border-ink/12 bg-cream lg:grid-cols-[1fr_1fr]">
        {/* Inputs */}
        <div className="border-b border-ink/10 p-7 lg:border-b-0 lg:border-r">
          <div className="flex flex-wrap items-center gap-3">
            <p className="text-xs font-bold uppercase tracking-[0.18em] text-teal-700">
              Your numbers
            </p>
            <span className="rounded-full border border-ink/15 px-2.5 py-0.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-ink-600">
              Illustrative scenario
            </span>
          </div>
          <h3 className="mt-2 text-xl font-semibold tracking-tight text-ink">
            See it for your store
          </h3>
          <p className="mt-2 text-xs leading-relaxed text-ink-600/80">
            An assumption-driven what-if, not a forecast and not a guaranteed return.
          </p>

          <label className="mt-6 block text-sm font-medium text-ink" htmlFor="roi-monthly-sales">
            Monthly sales (USD)
            <span className="ml-2 font-semibold text-teal-900 tabular-nums">{fmtUsd(monthly)}</span>
          </label>
          <input
            id="roi-monthly-sales"
            type="range"
            min={20_000}
            max={400_000}
            step={5_000}
            value={monthly}
            onChange={(e) => setMonthly(Number(e.target.value))}
            className="mt-2 h-11 w-full cursor-pointer accent-teal-600"
            aria-label="Monthly sales in US dollars"
          />
          <p className="mt-3 text-xs leading-relaxed text-ink-600/80">
            Estimated shrink applies the NRF survey's five-year average of 1.5% of annual
            retail sales to the revenue you enter. The same report lists a 1.4% FY2021 average.
            {' '}
            <a
              href="https://nrf.com/research/national-retail-security-survey-2022"
              target="_blank"
              rel="noreferrer"
              className="rounded-sm font-medium text-teal-700 underline underline-offset-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-600"
            >
              Read the NRF survey
            </a>.
          </p>

          <label className="mt-6 block text-sm font-medium text-ink" htmlFor="roi-avoidable-share">
            Assumed avoidable share of that shrink
            <span className="ml-2 font-semibold text-teal-900 tabular-nums">{Math.round(recovery * 100)}%</span>
          </label>
          <input
            id="roi-avoidable-share"
            type="range"
            min={0.1}
            max={0.5}
            step={0.05}
            value={recovery}
            onChange={(e) => setRecovery(Number(e.target.value))}
            className="mt-2 h-11 w-full cursor-pointer accent-teal-600"
            aria-label="Assumed avoidable share of total estimated shrink"
          />
          <p className="mt-3 text-xs leading-relaxed text-ink-600/80">
            No published study verifies how much loss a monitored floor avoids, so this
            slider is your own assumption. It applies to the total estimated shrink above —
            adjust it to whatever you consider realistic.
          </p>
        </div>

        {/* Output */}
        <div className="flex flex-col justify-center gap-4 bg-band p-7 text-band-text">
          <div>
            <p className="text-xs uppercase tracking-wider text-band-accent">Estimated annual shrink at this volume</p>
            <p className="mt-1 text-2xl font-semibold tracking-tight tabular-nums">{fmtUsd(est.annualShrink)}</p>
          </div>
          <div className="border-y border-white/12 py-5">
            <p className="text-xs uppercase tracking-wider text-band-accent">Illustrative avoidable loss per year</p>
            <p className="mt-1 text-3xl font-bold tracking-tight text-band-text tabular-nums">{fmtUsd(est.recoverable)}</p>
            <p className="mt-2 text-xs leading-relaxed text-band-muted">
              The share you assumed, applied to the estimated shrink — before any hardware,
              software, or pilot costs. Not net savings.
            </p>
          </div>
          <LinkButton href="#contact" onClick={scrollToSection('contact')} size="md" className="mt-1 w-full">
            Book a pilot
            <ArrowRight size={16} weight="bold" aria-hidden="true" />
          </LinkButton>
        </div>
      </div>

      <p className="mt-5 max-w-[80ch] text-xs leading-relaxed text-ink-600/80">
        The benchmark cards above are published industry averages from NACS, NRF, the FBI, and
        Jack L. Hayes — they describe the industry, not your store. The avoidable-share slider is
        an assumption with no published source, so the calculator output is an illustrative
        scenario. A pilot on your own cameras is what turns these into measured numbers.
      </p>
    </SectionShell>
  )
}
