import { ShieldCheck, Lock } from '@phosphor-icons/react'

interface Pillar {
  icon: React.ReactNode
  headline: string
  body: string
}

const PILLARS: Pillar[] = [
  {
    icon: <ShieldCheck size={22} weight="duotone" />,
    headline: 'Triage, not guesswork',
    body: 'The camera tells you who lingered and where they went — the moments worth a staff glance. It surfaces attention, not accusations.',
  },
  {
    icon: <Lock size={22} weight="duotone" />,
    headline: 'Runs on your hardware',
    body: 'Video never leaves your building. No cloud subscription, no third-party server, no monthly data fees. The footage — and the savings — stay yours.',
  },
]

export default function HonestFramingStrip() {
  return (
    <section className="bg-cream-200 border-y border-ink/8 px-6 py-16 md:py-20">
      <div className="mx-auto max-w-[1200px]">
        <div className="mb-10 max-w-2xl">
          <p className="mb-3 text-xs font-bold uppercase tracking-[0.18em] text-teal-600">How the system thinks</p>
          <h2 className="text-2xl md:text-3xl font-semibold tracking-tight leading-tight text-ink">
            It shows you where to look. You decide what happened.
          </h2>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {PILLARS.map((p) => (
            <div key={p.headline} className="bg-cream rounded-xl border border-ink/8 p-6 flex flex-col gap-3">
              <span className="text-teal-600">{p.icon}</span>
              <h3 className="text-base font-semibold tracking-tight text-ink">{p.headline}</h3>
              <p className="text-sm leading-relaxed text-ink-600">{p.body}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}
