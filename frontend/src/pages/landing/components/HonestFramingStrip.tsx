import { Lock, ShieldCheck } from '@phosphor-icons/react'

const PRINCIPLES = [
  {
    Icon: ShieldCheck,
    label: 'Decision model',
    title: 'Triage, not accusation.',
    body: 'Ketch shows the owner the moment worth reviewing. The owner decides whether the event is real, harmless, or needs action.',
  },
  {
    Icon: Lock,
    label: 'Privacy model',
    title: 'Footage stays under store control.',
    body: 'The production architecture is designed for on-prem processing, with alert evidence exposed only through controlled review flows.',
  },
]

export default function HonestFramingStrip() {
  return (
    <section className="border-t border-ink/10 bg-cream-200 px-5 py-20 sm:px-8 md:py-24">
      <div data-reveal-group className="mx-auto grid max-w-[1280px] border-y border-ink/12 md:grid-cols-2">
        {PRINCIPLES.map(({ Icon, label, title, body }, index) => (
          <article
            data-reveal-item
            key={label}
            className={`py-8 md:px-10 md:py-12 ${index > 0 ? 'border-t border-ink/12 md:border-l md:border-t-0' : ''}`}
          >
            <div className="flex items-center gap-3 text-teal-700">
              <Icon size={21} weight="duotone" aria-hidden="true" />
              <span className="text-[10px] font-semibold uppercase tracking-[0.2em]">{label}</span>
            </div>
            <h2 className="mt-8 max-w-[18ch] text-2xl font-semibold leading-tight tracking-[-0.035em] text-ink md:text-3xl">{title}</h2>
            <p className="mt-4 max-w-[52ch] text-sm leading-relaxed text-ink-600 md:text-base">{body}</p>
          </article>
        ))}
      </div>
    </section>
  )
}
