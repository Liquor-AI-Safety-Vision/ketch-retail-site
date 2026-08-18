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
 * industry figures, the output is labelled illustrative — never a claim about
 * the owner's actual store.
 */

const COST_CARDS = [
  {
    Icon: ChartLineDown,
    stat: fmtUsd(BENCHMARKS.retail.cstoreShrinkPerStoreUsd),
    label: 'shrink per store, per year',
    note: 'About a third of it employee theft — the steady leak behind the counter, not the front door.',
    src: 'NACS State of the Industry · NRF',
  },
  {
    Icon: UserCircleMinus,
    stat: fmtUsd(BENCHMARKS.retail.avgEmployeeCaseUsd),
    label: 'per dishonest-employee case',
    note: 'Average recovered once a case is caught — and most run for months before anyone notices.',
    src: 'Jack L. Hayes Retail Theft Survey, 2022',
  },
  {
    Icon: HandGrabbing,
    stat: '6.6%',
    label: 'of U.S. robberies hit c-stores',
    note: 'Registers are kept low by design — so the real cost is how often it happens, and how violent.',
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
      title="What theft quietly costs a store like yours"
      intro="Most loss isn't the dramatic robbery — it's the steady leak behind the counter that no one is watching for. Here's what the industry numbers say, and what even a fraction of it is worth recovering."
    >
      {/* Cost benchmark cards */}
      <div className="grid grid-cols-1 gap-5 md:grid-cols-3">
        {COST_CARDS.map((c) => (
          <div key={c.label} className="flex flex-col gap-3 rounded-xl border border-ink/10 bg-cream p-6">
            <c.Icon size={26} weight="duotone" className="text-red" />
            <p className="text-3xl font-semibold tracking-tight text-ink">{c.stat}</p>
            <p className="-mt-1 text-sm font-medium text-ink">{c.label}</p>
            <p className="text-sm leading-relaxed text-ink-600">{c.note}</p>
            <p className="mt-auto pt-2 text-[11px] font-medium text-ink-600/80">{c.src}</p>
          </div>
        ))}
      </div>

      {/* Calculator */}
      <div className="mt-8 grid grid-cols-1 gap-0 overflow-hidden rounded-2xl border border-ink/10 bg-cream lg:grid-cols-[1fr_1fr]">
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
            A deliberately conservative starting point — what steady attention and early catches alone tend
            to return. Drag it to your own number.
          </p>
        </div>

        {/* Output */}
        <div className="flex flex-col justify-center gap-4 bg-teal-900 p-7 text-cream">
          <div>
            <p className="text-xs uppercase tracking-wider text-teal-300">Est. annual shrink at this volume</p>
            <p className="text-2xl font-semibold tracking-tight">{fmtUsd(est.annualShrink)}</p>
          </div>
          <div className="rounded-lg bg-cream/10 p-4">
            <p className="text-xs uppercase tracking-wider text-teal-300">What a watched floor could recover / year</p>
            <p className="text-3xl font-bold tracking-tight text-cream">{fmtUsd(est.recoverable)}</p>
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
        Grounded in published industry data — NACS, NRF, the FBI, and Jack L. Hayes. These are benchmarks;
        your pilot turns them into your real numbers, measured on your own cameras.
      </p>
    </SectionShell>
  )
}
