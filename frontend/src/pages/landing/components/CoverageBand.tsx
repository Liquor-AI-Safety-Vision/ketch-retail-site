import { Lock, SignOut, Wine } from '@phosphor-icons/react'
import { SectionShell } from '../../../components/ui'

const CAPABILITIES = [
  {
    Icon: Wine,
    index: '01',
    title: 'High-value zones',
    body: 'Map shelves and aisles where shrink matters most, then surface dwell patterns that deserve a quick look.',
  },
  {
    Icon: SignOut,
    index: '02',
    title: 'Checkout and exits',
    body: 'Keep the route from floor to register to door visible in one event sequence for faster review.',
  },
  {
    Icon: Lock,
    index: '03',
    title: 'Restricted areas',
    body: 'Apply store-specific access rules to stockrooms, counters, kitchens, or any staff-only area.',
  },
]

export default function CoverageBand() {
  return (
    <SectionShell
      id="coverage"
      tone="teal"
      title={(
        <>
          <span className="block lg:whitespace-nowrap">Rules shaped around the store,</span>
          <span className="block lg:whitespace-nowrap">not a generic camera model.</span>
        </>
      )}
      titleClassName="max-w-none text-[clamp(2rem,3.25vw,2.75rem)]"
      intro="Each deployment starts with the floor plan, the camera viewpoints, and the owner's definition of what deserves attention."
    >
      <div data-reveal-group className="grid border-y border-band-border md:grid-cols-3">
        {CAPABILITIES.map(({ Icon, index, title, body }, position) => (
          <article
            data-reveal-item
            key={title}
            className={`py-7 md:min-h-[250px] md:px-8 md:py-9 ${position > 0 ? 'border-t border-band-border md:border-l md:border-t-0' : ''}`}
          >
            <div className="flex items-center justify-between">
              <Icon size={26} weight="duotone" className="text-band-accent" aria-hidden="true" />
              <span className="text-[11px] font-semibold tracking-[0.08em] text-band-faint">{index}</span>
            </div>
            <h3 className="mt-12 text-xl font-semibold tracking-[-0.025em] text-band-text">{title}</h3>
            <p className="mt-3 max-w-[36ch] text-sm leading-relaxed text-band-muted">{body}</p>
          </article>
        ))}
      </div>
    </SectionShell>
  )
}
