import { SectionShell } from '../../../components/ui'

const MEASURES = [
  {
    label: 'Review volume',
    measure: 'Events surfaced per shift',
    purpose: 'Shows whether the system creates a manageable queue instead of alert noise.',
  },
  {
    label: 'Owner validation',
    measure: 'Confirmed vs. dismissed',
    purpose: 'Creates the store-specific acceptance signal and identifies false-positive patterns.',
  },
  {
    label: 'Operational value',
    measure: 'Incidents found and time saved',
    purpose: 'Connects camera monitoring to outcomes the owner can actually verify during the pilot.',
  },
]

export default function ResultsSection() {
  return (
    <SectionShell
      id="results"
      title="Success is measured in the store, not in a demo deck."
      intro="The pilot establishes a baseline, records every owner decision, and evaluates whether the signal is accurate and useful enough for daily operations."
    >
      <div data-reveal-group className="border-y border-ink/12">
        {MEASURES.map((item, index) => (
          <div
            data-reveal-item
            key={item.label}
            className="grid gap-3 border-b border-ink/10 py-7 last:border-b-0 md:grid-cols-[56px_0.38fr_0.62fr_1fr] md:items-start md:gap-7"
          >
            <span className="text-xs font-semibold tracking-[0.18em] text-teal-600">0{index + 1}</span>
            <p className="text-xs font-semibold uppercase tracking-[0.14em] text-ink-600">{item.label}</p>
            <h3 className="text-lg font-semibold tracking-[-0.025em] text-ink">{item.measure}</h3>
            <p className="max-w-[58ch] text-sm leading-relaxed text-ink-600">{item.purpose}</p>
          </div>
        ))}
      </div>
    </SectionShell>
  )
}
