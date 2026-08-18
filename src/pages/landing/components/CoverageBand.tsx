import { SectionShell } from '../../../components/ui'
import { Wine, SignOut, Lock } from '@phosphor-icons/react'

interface Capability {
  icon: React.ReactNode
  title: string
  body: string
}

const CAPABILITIES: Capability[] = [
  {
    icon: <Wine size={28} weight="duotone" />,
    title: 'High-value zones',
    body: 'Spirits shelves, electronics, any aisle where shrink concentrates. A dwell timer flags lingering that\'s worth a staff glance.',
  },
  {
    icon: <SignOut size={28} weight="duotone" />,
    title: 'Checkout & exits',
    body: 'The system maps the path from shelf to door, and flags when someone reaches the exit without passing a register.',
  },
  {
    icon: <Lock size={28} weight="duotone" />,
    title: 'Restricted areas',
    body: 'Staff-only counters, stockrooms, tool storage. The moment someone crosses into a zone they shouldn\'t be in, it surfaces for review.',
  },
]

export default function CoverageBand() {
  return (
    <SectionShell
      id="coverage"
      tone="teal"
      eyebrow="Where it runs"
      title="One engine. Anywhere you have fixed cameras."
      intro="Define the zones that matter — high-value shelves, checkout lanes, exits, staff-only areas — and the same detection engine watches them. The pilot tunes every boundary to your floor plan."
    >
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        {CAPABILITIES.map((c) => (
          <div
            key={c.title}
            className="rounded-xl p-6 border flex flex-col gap-3 bg-teal-600/20 border-teal-300/20"
          >
            <span className="text-teal-300">{c.icon}</span>
            <h3 className="text-lg font-semibold tracking-tight text-cream">{c.title}</h3>
            <p className="text-sm leading-relaxed text-cream/70">{c.body}</p>
          </div>
        ))}
      </div>
    </SectionShell>
  )
}
