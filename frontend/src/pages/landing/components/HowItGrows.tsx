import { CheckCircle, Package, Pulse } from '@phosphor-icons/react'
import { SectionShell } from '../../../components/ui'

/* Status honesty: the two model layers below are PLANNED work evaluated
   against pilot footage — they are not part of the base deployment and are
   labelled that way. Retained evidence is a tuning goal, never an automatic
   accuracy improvement. */

const ADDONS = [
  {
    Icon: Package,
    label: 'Object context',
    title: 'SKU and object detection',
    description: 'Adds inventory-specific visual context around a tracked event using reference images supplied by the store.',
    boundary: 'Shows proximity and handling context, not proof of possession.',
  },
  {
    Icon: Pulse,
    label: 'Movement context',
    title: 'Behavior classification',
    description: 'Compares motion sequences with confirmed clips from the same environment to surface similar events for review.',
    boundary: 'Classifies movement patterns, not a person’s intent.',
  },
]

function EvidenceFrame({
  label,
  title,
  src,
  camera,
  width,
  height,
}: {
  label: string
  title: string
  src: string
  camera: string
  width: number
  height: number
}) {
  return (
    <figure className="overflow-hidden rounded-lg border border-ink/12 bg-cream-200">
      <div className="flex items-center justify-between border-b border-ink/10 px-4 py-3">
        <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-teal-700">{label}</span>
        <span className="text-[11px] text-ink-600">{camera}</span>
      </div>
      <div className="relative aspect-video overflow-hidden bg-ink">
        <img
          src={src}
          alt={title}
          width={width}
          height={height}
          loading="lazy"
          decoding="async"
          className="absolute inset-0 h-full w-full object-cover"
        />
      </div>
      <figcaption className="min-h-[76px] px-4 py-4 text-sm leading-relaxed text-ink-600">{title}</figcaption>
    </figure>
  )
}

export default function HowItGrows() {
  return (
    <SectionShell
      id="how-it-grows"
      eyebrow="System roadmap"
      title={(
        <>
          <span className="block lg:whitespace-nowrap">Start with monitoring.</span>
          <span className="block lg:whitespace-nowrap">Add intelligence only where</span>
          <span className="block lg:whitespace-nowrap">it earns its place.</span>
        </>
      )}
      titleClassName="max-w-none text-[clamp(2rem,3.25vw,2.75rem)]"
      intro="The base deployment establishes a reliable review workflow first. Every confirmed or dismissed alert is kept as a labeled example that can inform later tuning. Anything marked planned is future work, evaluated against real store footage before it ships."
    >
      <div data-reveal-group className="grid gap-5 md:grid-cols-2">
        <div data-reveal-item>
          <EvidenceFrame
            label="Included today"
            title="Continuous whole-store monitoring using the camera feeds already represented in the dashboard."
            src="/live-cameras/record-whole-store-153158.jpg"
            camera="Whole store / Camera 09"
            width={960}
            height={540}
          />
        </div>
        <div data-reveal-item>
          <EvidenceFrame
            label="Planned · evaluated in pilot"
            title="Confirmed footage is kept as reference material, with the goal of surfacing similar movement patterns later. The owner keeps the final decision either way."
            src="/demo-source/alerts/shoplifting/shoplifting-review.jpg"
            camera="Video-backed alert"
            width={960}
            height={544}
          />
        </div>
      </div>

      <div className="mt-16 grid gap-10 lg:grid-cols-[0.72fr_1.28fr] lg:gap-20">
        <div data-reveal>
          <p className="text-sm font-semibold text-teal-700">Optional model layers</p>
          <h3 className="mt-5 max-w-[14ch] text-2xl font-semibold leading-tight tracking-[-0.035em] text-ink md:text-3xl">
            Planned. Post-pilot. Boundary included.
          </h3>
          <p className="mt-4 max-w-[42ch] text-sm leading-relaxed text-ink-600">
            These are not part of the base deployment. Each module states what it observes and what it cannot conclude — that boundary is part of the design, not a footnote.
          </p>
        </div>

        <div data-reveal-group className="border-y border-ink/12">
          {ADDONS.map(({ Icon, label, title, description, boundary }, index) => (
            <article data-reveal-item key={title} className="grid gap-4 border-b border-ink/10 py-7 last:border-b-0 md:grid-cols-[44px_0.75fr_1.25fr] md:gap-7">
              <Icon size={23} weight="duotone" className="text-teal-700" aria-hidden="true" />
              <div>
                <p className="text-xs font-semibold text-ink-600">0{index + 1} / {label} · planned</p>
                <h4 className="mt-2 text-lg font-semibold tracking-[-0.025em] text-ink">{title}</h4>
              </div>
              <div>
                <p className="text-sm leading-relaxed text-ink-600">{description}</p>
                <p className="mt-3 flex items-start gap-2 text-xs leading-relaxed text-ink-600">
                  <CheckCircle size={14} weight="fill" className="mt-0.5 shrink-0 text-teal-700" aria-hidden="true" />
                  {boundary}
                </p>
              </div>
            </article>
          ))}
        </div>
      </div>

    </SectionShell>
  )
}
