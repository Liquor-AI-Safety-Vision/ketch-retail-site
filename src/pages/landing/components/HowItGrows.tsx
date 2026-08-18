import { SectionShell } from '../../../components/ui'
import {
  Package,
  Pulse,
  ReceiptX,
  ArrowsClockwise,
  CheckCircle,
} from '@phosphor-icons/react'

/* ── Phase panels ── */
function PhasePanel({
  phase,
  label,
  tag,
  tagStyle,
  items,
  src,
}: {
  phase: string
  label: string
  tag: string
  tagStyle: string
  items: React.ReactNode[]
  src: string
}) {
  return (
    <div className="flex flex-col h-full">
      <div className="mb-3 flex items-center gap-2">
        <span className={`text-[11px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-md ${tagStyle}`}>{tag}</span>
        <span className="text-xs text-ink-600">{phase}</span>
      </div>
      <div className="rounded-xl border border-ink/10 bg-cream-200 overflow-hidden flex-1">
        {/* Camera frame — real CCTV poster with burned-in amber loiter box */}
        <div className="relative aspect-video overflow-hidden">
          <img src={src} alt="" className="absolute inset-0 h-full w-full object-cover" />
          <div className="absolute top-2 left-2 z-10 flex items-center gap-1.5 bg-ink/60 rounded-md px-2 py-0.5 text-cream text-[10px] font-semibold tracking-wide">
            <span className="h-1.5 w-1.5 rounded-full bg-red motion-safe:animate-pulse" />
            CAM 01 · Spirits Aisle
          </div>
          {/* Phase-2 extra overlays */}
          {items.includes('sku') && (
            <div className="absolute top-1/3 right-8 z-10 border border-dashed border-amber rounded-sm px-1.5 py-0.5 text-[9px] font-bold text-amber bg-amber/10">
              $$$ high-value
            </div>
          )}
          {items.includes('behavior') && (
            <div className="absolute bottom-4 left-0 right-0 z-10 flex justify-center">
              <span className="bg-ink/70 text-cream text-[9px] px-2 py-0.5 rounded font-mono">
                carried past register · not returned to shelf
              </span>
            </div>
          )}
          {items.includes('pos') && (
            <div className="absolute bottom-2 right-2 z-10 bg-red/90 text-cream text-[9px] px-2 py-0.5 rounded font-bold">
              Register: 0 scanned · 1 leaving · ✖ unpaid
            </div>
          )}
        </div>
        {/* Caption */}
        <div className="p-3">
          <p className="text-xs font-semibold text-ink mb-0.5">{label}</p>
        </div>
      </div>
    </div>
  )
}

/* ── Add-on cards ── */
interface Addon {
  icon: React.ReactNode
  number: string
  title: string
  whatItKnows: string
  example: string
  honesty: string
}

const ADDONS: Addon[] = [
  {
    icon: <Package size={20} weight="duotone" />,
    number: '1',
    title: 'SKU / object detection',
    whatItKnows: 'What',
    example: '"$$$ high-value item" near subject — trained on your inventory photos.',
    honesty: 'Shows item proximity, not possession. No specific dollar figure until your SKU data is loaded.',
  },
  {
    icon: <Pulse size={20} weight="duotone" />,
    number: '2',
    title: 'Behavior classification',
    whatItKnows: 'Action',
    example: '"carried past register · not returned to shelf" — video embedding over the person\'s path.',
    honesty: 'Classifies movement, not intent. Accuracy improves with your 30-day archive of confirmed clips.',
  },
  {
    icon: <ReceiptX size={20} weight="duotone" />,
    number: '3',
    title: 'POS fusion',
    whatItKnows: 'Verdict',
    example: '"Register: 0 scanned · 1 leaving · ✖ unpaid" — the only module that turns triage into a confirmed incident.',
    honesty: 'This is the only verdict source. Without POS, the system flags for your staff to check — never accuses.',
  },
]

/* ── Flywheel diagram ── */
function FlywheelDiagram() {
  const nodes = [
    { angle: -60, label: 'Camera captures your footage', sub: 'every aisle, 24/7' },
    { angle: 60, label: 'Alert fires', sub: 'dwell + exit pattern' },
    { angle: 180, label: 'Staff confirms or dismisses', sub: 'the oracle' },
  ]

  return (
    <div className="relative flex flex-col items-center">
      <div className="relative w-72 h-72">
        {/* Center hub */}
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="h-24 w-24 rounded-full bg-teal-900 flex flex-col items-center justify-center border-4 border-teal-600/30 shadow-lg">
            <ArrowsClockwise size={20} className="text-teal-300 mb-1 motion-safe:animate-[spin_22s_linear_infinite]" />
            <span className="text-[10px] font-bold text-cream text-center leading-tight px-1">
              Smarter<br/>over time
            </span>
          </div>
        </div>

        {/* Orbit ring */}
        <div className="absolute inset-6 rounded-full border-2 border-dashed border-teal-600/30 motion-safe:animate-[spin_22s_linear_infinite]" />

        {/* Nodes */}
        {nodes.map((n, i) => {
          const rad = (n.angle * Math.PI) / 180
          const cx = 50 + 42 * Math.cos(rad)
          const cy = 50 + 42 * Math.sin(rad)
          return (
            <div
              key={i}
              className="absolute flex flex-col items-center"
              style={{
                left: `${cx}%`,
                top: `${cy}%`,
                transform: 'translate(-50%, -50%)',
              }}
            >
              <div
                className="h-8 w-8 rounded-full bg-teal-600 flex items-center justify-center shadow-md motion-safe:animate-[nodePulse_2.4s_ease-in-out_infinite]"
                style={{ animationDelay: `${i * 0.8}s` }}
              >
                <span className="text-cream text-xs font-bold">{i + 1}</span>
              </div>
            </div>
          )
        })}
      </div>

      {/* Labels below */}
      <div className="mt-4 flex flex-col gap-1.5 w-full max-w-xs">
        {nodes.map((n, i) => (
          <div key={i} className="flex items-start gap-2.5 text-sm">
            <span className="h-5 w-5 min-w-[1.25rem] rounded-full bg-teal-600 flex items-center justify-center text-cream text-[10px] font-bold mt-0.5">
              {i + 1}
            </span>
            <div>
              <span className="font-medium text-cream">{n.label}</span>
              <span className="text-cream/70"> — {n.sub}</span>
            </div>
          </div>
        ))}
        <div className="flex items-start gap-2.5 text-sm mt-1">
          <span className="h-5 w-5 min-w-[1.25rem] rounded-full bg-teal-900 flex items-center justify-center text-teal-300 text-[10px] font-bold mt-0.5">
            4
          </span>
          <div>
            <span className="font-medium text-cream">Labels feed training</span>
            <span className="text-cream/70"> — your 30-day archive, uncertainty sampling</span>
          </div>
        </div>
      </div>
    </div>
  )
}

export default function HowItGrows() {
  return (
    <SectionShell
      id="how-it-grows"
      eyebrow="Modular upgrades"
      title="After we train on your store."
      intro="Start with base triage. Add modules as your confidence grows — each one bought separately, each one sharpening what the previous phase can see."
    >
      {/* Before / After panels */}
      <div className="mb-12">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <PhasePanel
            phase="Phase 1 · Base pilot"
            label="Zone + dwell + walkout rules — geometric, runs day one."
            tag="Rule-based · live now"
            tagStyle="bg-teal-900 text-teal-300"
            items={[]}
            src="/demo/lingering/poster.jpg"
          />
          <div className="flex flex-col h-full">
            <div className="mb-3 flex items-center gap-2">
              <span className="text-[11px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-md bg-cream-200 text-ink-600 border border-ink/12">
                Behavior model · after training
              </span>
              <span className="text-xs text-ink-600">Phase 2 · trained on your store</span>
            </div>
            <div className="rounded-xl border border-dashed border-ink/20 bg-cream-200/50 overflow-hidden flex-1">
              <div className="relative aspect-video overflow-hidden">
                <img src="/demo/lingering/phase2_clean.jpg" alt="" className="absolute inset-0 h-full w-full object-cover" />

                {/* CAM badge */}
                <div className="absolute top-2 left-2 z-20 flex items-center gap-1.5 bg-ink/60 rounded-md px-2 py-0.5 text-cream text-[10px] font-semibold tracking-wide">
                  <span className="h-1.5 w-1.5 rounded-full bg-red motion-safe:animate-pulse" />
                  CAM 01 · Spirits Aisle
                </div>

                {/* Tight action box on the person (behavior model, not a zone) */}
                <div className="absolute z-10 rounded-sm border-2 border-amber"
                     style={{ left: '26.3%', top: '26.7%', width: '19.6%', height: '72.6%' }}>
                  <span className="absolute -top-4 left-0 whitespace-nowrap rounded-sm bg-amber px-1 py-px text-[9px] font-bold text-ink">
                    Track 1 · reaching
                  </span>
                </div>

                {/* Behavior readout panel */}
                <div className="absolute bottom-2 right-2 z-20 w-[58%] max-w-[230px] rounded-md bg-ink/80 px-2.5 py-2 backdrop-blur-sm">
                  <p className="mb-1 text-[9px] font-bold uppercase tracking-wider text-cream/70">Behavior read · Track 1</p>
                  {/* Likelihood meter */}
                  <div className="mb-1.5 flex items-center gap-2">
                    <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-cream/15">
                      <div className="h-full rounded-full bg-amber" style={{ width: '72%' }} />
                    </div>
                    <span className="text-[10px] font-bold text-amber">0.72</span>
                  </div>
                  <p className="text-[9px] leading-snug text-cream/80">
                    reach + retrieve · high-value shelf · no basket · posture matches review pattern
                  </p>
                </div>
              </div>
              <div className="p-3">
                <p className="text-xs font-semibold text-ink mb-0.5">Trained on your footage, it scores intent from posture and motion — a likelihood to review, never a verdict.</p>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Add-on cards */}
      <div className="mb-16">
        <p className="text-xs font-bold uppercase tracking-[0.18em] text-teal-600 mb-6">Three modular add-ons</p>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
          {ADDONS.map((a) => (
            <div key={a.number} className="rounded-xl border border-ink/10 bg-cream p-5 flex flex-col gap-3">
              <div className="flex items-start gap-3">
                <span className="text-teal-600 mt-0.5">{a.icon}</span>
                <div className="flex-1">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-[10px] font-bold uppercase tracking-wider text-teal-600">Add-on {a.number}</span>
                    <span className="text-[10px] font-bold uppercase tracking-wider text-ink-600/60">· Knows the {a.whatItKnows}</span>
                  </div>
                  <h3 className="text-sm font-semibold tracking-tight text-ink">{a.title}</h3>
                </div>
              </div>
              <p className="text-sm leading-relaxed text-ink-600">{a.example}</p>
              {/* Honesty note */}
              <div className="mt-auto pt-2 border-t border-ink/8 flex items-start gap-1.5">
                <CheckCircle size={13} className="text-teal-600 mt-0.5 shrink-0" weight="fill" />
                <p className="text-xs text-ink-600/70 leading-snug">{a.honesty}</p>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Flywheel */}
      <div className="rounded-2xl bg-teal-900 border border-teal-600/20 p-8 md:p-10">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-10 items-center">
          <div>
            <p className="text-xs font-bold uppercase tracking-[0.18em] text-teal-300 mb-3">The data flywheel</p>
            <h3 className="text-xl md:text-2xl font-semibold tracking-tight text-cream leading-snug mb-4">
              Every confirm or dismiss makes the next alert more precise.
            </h3>
            <p className="text-sm leading-relaxed text-cream/70 max-w-[48ch]">
              Your staff acts as the oracle. When they review a flagged event and mark it confirmed or
              dismissed, that label feeds back into training. The system surfaces its least-certain
              alerts first — so human effort goes exactly where it matters.
            </p>
            <div className="mt-6 flex flex-col gap-2.5">
              <div className="flex items-center gap-2.5 text-sm text-cream/80">
                <CheckCircle size={16} className="text-teal-300 shrink-0" weight="fill" />
                <span>30-day archive from your own cameras</span>
              </div>
              <div className="flex items-center gap-2.5 text-sm text-cream/80">
                <CheckCircle size={16} className="text-teal-300 shrink-0" weight="fill" />
                <span>Human confirm/dismiss = ground truth labels</span>
              </div>
              <div className="flex items-center gap-2.5 text-sm text-cream/80">
                <CheckCircle size={16} className="text-teal-300 shrink-0" weight="fill" />
                <span>Gets sharper every week it runs.</span>
              </div>
            </div>
          </div>
          <div className="flex justify-center">
            <FlywheelDiagram />
          </div>
        </div>
      </div>
    </SectionShell>
  )
}
