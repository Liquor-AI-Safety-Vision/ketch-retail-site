import { ArrowsClockwise, CheckCircle, Package, Pulse } from '@phosphor-icons/react'
import { SectionShell } from '../../../components/ui'

/* Status honesty: the two model layers below are PLANNED work evaluated
   against pilot footage — they are not part of the base deployment and are
   labelled that way. The flywheel describes retained evidence and a tuning
   goal, never an automatic accuracy improvement. */

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

const FLYWHEEL_STEPS = [
  {
    title: 'Footage is captured',
    detail: 'Monitored areas, throughout the day.',
  },
  {
    title: 'An alert is prepared',
    detail: 'Event plus surrounding video, ready to review.',
  },
  {
    title: 'The owner decides',
    detail: 'Confirmed or dismissed — the human call.',
  },
  {
    title: 'Evidence is retained',
    detail: 'Labeled examples that can inform future tuning.',
  },
]

function FlywheelDiagram() {
  const nodes = [
    { angle: -45 },
    { angle: 45 },
    { angle: 135 },
    { angle: 225 },
  ]

  return (
    <div className="relative h-72 w-72" role="img" aria-label="Evidence feedback loop diagram">
      <div className="absolute inset-0 flex items-center justify-center">
        <div className="flex h-24 w-24 flex-col items-center justify-center rounded-full border-4 border-teal-600/30 bg-teal-900 shadow-lg">
          <ArrowsClockwise size={20} className="mb-1 text-teal-300 motion-safe:animate-[spin_22s_linear_infinite]" aria-hidden="true" />
          <span className="px-1 text-center text-[10px] font-bold leading-tight text-cream">
            Owner-labeled<br />evidence
          </span>
        </div>
      </div>

      <div className="absolute inset-6 rounded-full border-2 border-dashed border-teal-600/30 motion-safe:animate-[spin_22s_linear_infinite]" />

      {nodes.map((node, index) => {
        const radians = (node.angle * Math.PI) / 180
        const x = 50 + 42 * Math.cos(radians)
        const y = 50 + 42 * Math.sin(radians)

        return (
          <div
            key={node.angle}
            className="absolute flex flex-col items-center"
            style={{
              left: `${x}%`,
              top: `${y}%`,
              transform: 'translate(-50%, -50%)',
            }}
          >
            <div
              className="flex h-8 w-8 items-center justify-center rounded-full bg-teal-600 shadow-md motion-safe:animate-[nodePulse_2.4s_ease-in-out_infinite]"
              style={{ animationDelay: `${index * 0.6}s` }}
            >
              <span className="text-xs font-bold text-cream">{index + 1}</span>
            </div>
          </div>
        )
      })}
    </div>
  )
}

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
        <span className="text-[10px] font-semibold uppercase tracking-[0.18em] text-teal-700">{label}</span>
        <span className="text-[10px] text-ink-600">{camera}</span>
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
      intro="The base deployment establishes a reliable review workflow first. Anything below marked planned is future work, evaluated against real store footage before it ships."
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
                <p className="text-xs font-semibold text-ink-600/70">0{index + 1} / {label} · planned</p>
                <h4 className="mt-2 text-lg font-semibold tracking-[-0.025em] text-ink">{title}</h4>
              </div>
              <div>
                <p className="text-sm leading-relaxed text-ink-600">{description}</p>
                <p className="mt-3 flex items-start gap-2 text-xs leading-relaxed text-ink-600/75">
                  <CheckCircle size={14} weight="fill" className="mt-0.5 shrink-0 text-teal-700" aria-hidden="true" />
                  {boundary}
                </p>
              </div>
            </article>
          ))}
        </div>
      </div>

      <section className="relative left-1/2 mt-16 w-[100dvw] -translate-x-1/2 overflow-hidden border-y border-band-border bg-band px-5 py-14 text-band-text sm:px-8 md:py-20">
        <div className="mx-auto max-w-[1280px]">
          <div data-reveal className="grid max-w-5xl gap-5 md:grid-cols-[minmax(0,0.42fr)_minmax(0,0.58fr)] md:gap-12">
            <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-band-accent">The data flywheel</p>
            <div>
              <h3 className="max-w-none text-[clamp(2rem,3.25vw,2.75rem)] font-semibold leading-[1.08] tracking-[-0.035em]">
                <span className="block lg:whitespace-nowrap">Owner decisions build the</span>
                <span className="block lg:whitespace-nowrap">evidence base for future tuning.</span>
              </h3>
              <p className="mt-5 max-w-[58ch] text-base leading-relaxed text-band-muted md:text-lg">
                The owner remains the final decision-maker. Every reviewed event is retained as a labeled
                example, and with enough confirmed footage that record can inform how future versions
                prioritize review — an evaluation goal for the pilot, not an automatic improvement.
              </p>
            </div>
          </div>

          <div className="mt-10 grid border-y border-band-border md:mt-12 lg:grid-cols-[0.9fr_1.1fr]">
            <div data-reveal className="flex min-h-[320px] items-center justify-center border-b border-band-border py-9 lg:border-b-0 lg:border-r">
              <FlywheelDiagram />
            </div>

            <ol data-reveal-group className="grid sm:grid-cols-2">
              {FLYWHEEL_STEPS.map(({ title, detail }, index) => (
                <li
                  data-reveal-item
                  key={title}
                  className={`grid min-h-[150px] grid-cols-[28px_1fr] content-center gap-3 border-band-border px-1 py-6 sm:px-8 ${index > 0 ? 'border-t' : ''} ${index === 1 ? 'sm:border-l sm:border-t-0' : ''} ${index === 3 ? 'sm:border-l' : ''}`}
                >
                  <span className="grid h-6 w-6 place-items-center rounded-full bg-band-accent text-[10px] font-bold text-band" aria-hidden="true">
                    {index + 1}
                  </span>
                  <p className="text-sm leading-6">
                    <strong className="font-semibold text-band-text">{title}</strong>
                    <span className="text-band-muted">: {detail}</span>
                  </p>
                </li>
              ))}
            </ol>
          </div>
        </div>
      </section>
    </SectionShell>
  )
}
