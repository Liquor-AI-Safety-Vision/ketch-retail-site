import { SectionShell, TierBadge } from '../../../components/ui'

interface ResultCard {
  tier: 1 | 2 | 3
  stat: string
  description: string
}

const RESULTS: ResultCard[] = [
  {
    tier: 2,
    stat: '~40 events / week',
    description: 'Early Warning flags in a mid-volume store — dwell patterns that merit a staff glance.',
  },
  {
    tier: 1,
    stat: '~6 events / week',
    description: 'Priority Alerts (lingered + moved to exit) escalated to the manager\'s phone.',
  },
  {
    tier: 3,
    stat: '<1 false alert / shift',
    description: 'Zone Breach noise after two weeks of staff confirm/dismiss tuning.',
  },
]

export default function ResultsSection() {
  return (
    <SectionShell
      id="results"
      eyebrow="What to expect"
      title="What a typical week looks like"
      intro="A sense of the signal volume your team would triage — early warnings worth a glance, priority alerts worth a call, and how fast the noise drops once staff start confirming."
    >
      <p className="mb-6 text-xs text-ink-600/60 italic">Illustrative figures, shown to set expectations — your pilot produces the real ones.</p>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
        {RESULTS.map((r) => (
          <div key={r.stat} className="rounded-xl border border-ink/10 bg-cream p-6 flex flex-col gap-3">
            <TierBadge tier={r.tier} variant="soft" />
            <p className="text-2xl font-semibold tracking-tight text-ink">{r.stat}</p>
            <p className="text-sm leading-relaxed text-ink-600">{r.description}</p>
          </div>
        ))}
      </div>
    </SectionShell>
  )
}
