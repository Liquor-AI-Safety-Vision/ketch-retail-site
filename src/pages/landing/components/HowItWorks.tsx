import {
  Camera,
  Person,
  Path,
  BellRinging,
} from '@phosphor-icons/react'
import { SectionShell } from '../../../components/ui'

interface Step {
  number: string
  icon: React.ReactNode
  title: string
  body: string
}

const STEPS: Step[] = [
  {
    number: '01',
    icon: <Camera size={24} weight="duotone" />,
    title: 'Your existing cameras',
    body: 'Connects to your current CCTV feed. No new hardware required for the pilot — we tune the model to your store\'s camera angles and lighting.',
  },
  {
    number: '02',
    icon: <Person size={24} weight="duotone" />,
    title: 'Person + zone tracking',
    body: 'Every person is tracked frame-by-frame. Defined zones — high-value shelf, checkout lane, exit door — are mapped to your floor plan.',
  },
  {
    number: '03',
    icon: <Path size={24} weight="duotone" />,
    title: 'Dwell and exit triage',
    body: 'When someone lingers in a high-value zone past a threshold, an Early Warning fires. If they then move toward the exit without going through checkout, a Priority Alert follows. Both are geometric facts — not accusations.',
  },
  {
    number: '04',
    icon: <BellRinging size={24} weight="duotone" />,
    title: 'Human confirms (the oracle)',
    body: 'Your staff receives an SMS or WhatsApp alert with a clip thumbnail. They check — paid or not paid? That confirm/dismiss feeds back into the system to reduce false positives over time.',
  },
]

export default function HowItWorks() {
  return (
    <SectionShell
      id="how-it-works"
      eyebrow="How it works"
      title="Four steps from camera to alert"
      intro="The system handles the watching. Your staff handles the judgment. The verdict always belongs to a human — and ultimately to your POS."
    >
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
        {STEPS.map((step) => (
          <div key={step.number} className="relative">
            {/* Step connector line (desktop) */}
            <div className="hidden lg:block absolute top-8 left-full w-full h-px bg-ink/10 -translate-x-1/2 z-0" />

            <div className="relative z-10 bg-cream-200 rounded-xl p-6 border border-ink/8 h-full flex flex-col">
              {/* Number + icon */}
              <div className="flex items-start gap-3 mb-4">
                <span className="text-xs font-bold tracking-[0.18em] text-teal-600 pt-0.5 min-w-[1.75rem]">
                  {step.number}
                </span>
                <span className="text-teal-900">{step.icon}</span>
              </div>
              <h3 className="text-base font-semibold tracking-tight text-ink mb-2">{step.title}</h3>
              <p className="text-sm leading-relaxed text-ink-600 flex-1">{step.body}</p>
            </div>
          </div>
        ))}
      </div>
    </SectionShell>
  )
}
