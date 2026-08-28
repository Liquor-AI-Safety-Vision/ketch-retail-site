import { useState } from 'react'
import { UserCircleMinus, HandGrabbing, ChartLineDown, ArrowRight } from '@phosphor-icons/react'
import { SectionShell, Button } from '../../../components/ui'
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
 * benchmarks), here's the slice a watched floor can deter, and a calculator
 * the owner drives with their own monthly sales. Honesty rule: benchmarks are
 * industry figures, the output is labelled illustrative, never a claim about
 * the owner's actual store.
 */

const COST_CARDS = [
  {
    Icon: ChartLineDown,
    stat: fmtUsd(BENCHMARKS.retail.cstoreShrinkPerStoreUsd),
    label: 'shrink per store, per year',
    note: 'About a third of it is employee theft: the steady leak behind the counter, not the front door.',
    src: 'NACS State of the Industry · NRF',
  },
  {
    Icon: UserCircleMinus,
    stat: fmtUsd(BENCHMARKS.retail.avgEmployeeCaseUsd),
    label: 'per dishonest-employee case',
    note: 'Average recovered once a case is caught. Many cases run for months before anyone notices.',
    src: 'Jack L. Hayes Retail Theft Survey, 2022',
  },
  {
    Icon: HandGrabbing,
    stat: '6.6%',
    label: 'of U.S. robberies hit c-stores',
    note: 'Registers are kept low by design, so the real cost is how often it happens and how violent it becomes.',
    src: 'FBI Crime in the U.S., 2019',
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
      intro="Most loss is not a dramatic robbery. It is the steady leak behind the counter that nobody is watching for. Published industry figures show what even a fraction is worth recovering."
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
              <c.Icon size={24} weight="duotone" className="text-red" />
              <span className="text-[10px] font-semibold tracking-[0.18em] text-ink-600/45">0{index + 1}</span>
            </div>
            <p className="mt-10 text-4xl font-semibold tracking-[-0.045em] text-ink">{c.stat}</p>
            <p className="mt-2 text-sm font-medium text-ink">{c.label}</p>
            <p className="mt-4 text-sm leading-relaxed text-ink-600">{c.note}</p>
            <p className="mt-auto pt-7 text-[10px] font-medium uppercase tracking-[0.08em] text-ink-600/65">{c.src}</p>
          </div>
        ))}
      </div>

      {/* Calculator */}
      <div className="mt-10 grid grid-cols-1 gap-0 overflow-hidden rounded-lg border border-ink/12 bg-cream lg:grid-cols-[1fr_1fr]">
        {/* Inputs */}
        <div className="border-b border-ink/10 p-7 lg:border-b-0 lg:border-r">
          <p className="text-xs font-bold uppercase tracking-[0.18em] text-teal-600">
            Your numbers
          </p>
          <h3 className="mt-2 text-xl font-semibold tracking-tight text-ink">
            See it for your store
          </h3>

          <label className="mt-6 block text-sm font-medium text-ink">
            Monthly sales
            <span className="ml-2 font-semibold text-teal-900">{fmtUsd(monthly)}</span>
          </label>
          <input
            type="range"
            min={20_000}
            max={400_000}
            step={5_000}
            value={monthly}
            onChange={(e) => setMonthly(Number(e.target.value))}
            className="mt-2 w-full accent-teal-600"
            aria-label="Monthly sales"
          />

          <label className="mt-6 block text-sm font-medium text-ink">
            Share of internal loss a watched floor deters
            <span className="ml-2 font-semibold text-teal-900">{Math.round(recovery * 100)}%</span>
          </label>
          <input
            type="range"
            min={0.1}
            max={0.5}
            step={0.05}
            value={recovery}
            onChange={(e) => setRecovery(Number(e.target.value))}
            className="mt-2 w-full accent-teal-600"
            aria-label="Recovery share"
          />
          <p className="mt-3 text-xs leading-relaxed text-ink-600/80">
            A deliberately conservative starting point based on what steady attention and early catches tend
            to return. Drag it to your own number.
          </p>
        </div>

        {/* Output */}
        <div className="flex flex-col justify-center gap-4 bg-band p-7 text-band-text">
          <div>
            <p className="text-xs uppercase tracking-wider text-band-accent">Est. annual shrink at this volume</p>
            <p className="text-2xl font-semibold tracking-tight">{fmtUsd(est.annualShrink)}</p>
          </div>
          <div className="border-y border-white/12 py-5">
            <p className="text-xs uppercase tracking-wider text-band-accent">What a watched floor could recover / year</p>
            <p className="text-3xl font-bold tracking-tight text-band-text">{fmtUsd(est.recoverable)}</p>
          </div>
          <a href="#contact" onClick={scrollToSection('contact')} className="mt-1">
            <Button size="md" className="w-full">
              Book a pilot
              <ArrowRight size={16} weight="bold" />
            </Button>
          </a>
        </div>
      </div>

      <p className="mt-5 text-xs leading-relaxed text-ink-600/80">
        Grounded in published industry data from NACS, NRF, the FBI, and Jack L. Hayes. These are benchmarks;
        your pilot turns them into your real numbers, measured on your own cameras.
      </p>
    </SectionShell>
  )
}
