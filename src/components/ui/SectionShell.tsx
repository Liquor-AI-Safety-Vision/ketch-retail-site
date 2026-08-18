import type { ReactNode } from 'react'

interface SectionShellProps {
  children: ReactNode
  id?: string
  eyebrow?: string
  title?: ReactNode
  intro?: ReactNode
  /** Background band. 'cream' default, 'teal' for dark CTA/coverage bands. */
  tone?: 'cream' | 'cream-200' | 'teal'
  className?: string
}

/* Consistent max-width + vertical rhythm for landing sections (density 5).
   Header block (eyebrow / title / intro) is optional. */
const TONES = {
  cream: 'bg-cream text-ink',
  'cream-200': 'bg-cream-200 text-ink',
  teal: 'bg-teal-900 text-cream',
}

export default function SectionShell({
  children, id, eyebrow, title, intro, tone = 'cream', className = '',
}: SectionShellProps) {
  const muted = tone === 'teal' ? 'text-cream/70' : 'text-ink-600'
  const accent = tone === 'teal' ? 'text-teal-300' : 'text-teal-600'
  return (
    <section id={id} className={`${TONES[tone]} px-6 py-20 md:py-28`}>
      <div className={`mx-auto max-w-[1200px] ${className}`}>
        {(eyebrow || title || intro) && (
          <div className="mb-12 max-w-2xl">
            {eyebrow && (
              <p className={`mb-3 text-xs font-bold uppercase tracking-[0.18em] ${accent}`}>{eyebrow}</p>
            )}
            {title && (
              <h2 className="text-3xl md:text-4xl font-semibold tracking-tight leading-tight">{title}</h2>
            )}
            {intro && <p className={`mt-4 text-lg leading-relaxed ${muted}`}>{intro}</p>}
          </div>
        )}
        {children}
      </div>
    </section>
  )
}
