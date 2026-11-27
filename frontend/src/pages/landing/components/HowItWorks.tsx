import { BellRinging, Camera, Path, Person } from '@phosphor-icons/react'
import { SectionShell } from '../../../components/ui'

const STEPS = [
  {
    number: '01',
    Icon: Camera,
    title: 'Connect the cameras already in place',
    body: 'The pilot connects to the current CCTV feed and maps each useful viewpoint. No camera replacement is required to begin.',
  },
  {
    number: '02',
    Icon: Person,
    title: 'Define people, zones, and operating rules',
    body: 'Staff, customers, checkout areas, stockrooms, and high-value shelves are represented as explicit rules for that store.',
  },
  {
    number: '03',
    Icon: Path,
    title: 'Surface the relevant sequence',
    body: 'The system follows the event across the available view and prepares the surrounding footage instead of asking the owner to search manually.',
  },
  {
    number: '04',
    Icon: BellRinging,
    title: 'Send evidence for a human decision',
    body: 'The owner receives a video-backed alert and decides what happened. The system highlights; it does not accuse or contact emergency services.',
  },
]

export default function HowItWorks() {
  return (
    <SectionShell
      id="how-it-works"
      title="From camera feed to a decision-ready clip."
      intro="The product is built around a simple division of labor: software keeps watch, while the owner keeps judgment and control."
    >
      <ol data-reveal-group className="border-y border-ink/12">
        {STEPS.map(({ number, Icon, title, body }) => (
          <li
            data-reveal-item
            key={number}
            className="grid gap-4 border-b border-ink/10 py-7 last:border-b-0 md:grid-cols-[64px_52px_minmax(220px,0.7fr)_minmax(0,1fr)] md:items-start md:gap-7 md:py-8"
          >
            <span className="text-xs font-semibold tracking-[0.18em] text-teal-700">{number}</span>
            <Icon size={25} weight="duotone" className="text-teal-900" aria-hidden="true" />
            <h3 className="max-w-[24ch] text-lg font-semibold leading-snug tracking-[-0.025em] text-ink md:text-xl">{title}</h3>
            <p className="max-w-[62ch] text-sm leading-relaxed text-ink-600 md:text-base">{body}</p>
          </li>
        ))}
      </ol>
    </SectionShell>
  )
}
