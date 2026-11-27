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
  titleClassName?: string
}

/* Consistent max-width + vertical rhythm for landing sections (density 5).
   Header block (eyebrow / title / intro) is optional. */
const TONES = {
  cream: 'bg-cream text-ink',
  'cream-200': 'bg-cream-200 text-ink',
  teal: 'bg-band text-band-text',
}

export default function SectionShell({
  children, id, eyebrow, title, intro, tone = 'cream', className = '', titleClassName,
}: SectionShellProps) {
  const muted = tone === 'teal' ? 'text-band-muted' : 'text-ink-600'
  const accent = tone === 'teal' ? 'text-band-accent' : 'text-teal-700'
  const headerLayout = eyebrow
    ? 'grid max-w-5xl gap-5 md:grid-cols-[minmax(0,0.42fr)_minmax(0,0.58fr)] md:gap-12'
    : 'max-w-4xl'

  return (
    <section id={id} className={`${TONES[tone]} border-t border-ink/8 px-5 py-20 sm:px-8 md:py-28`}>
      <div className={`mx-auto max-w-[1280px] ${className}`}>
        {(eyebrow || title || intro) && (
          <div data-reveal className={`mb-12 md:mb-16 ${headerLayout}`}>
            {eyebrow && (
              <p className={`text-[11px] font-semibold uppercase tracking-[0.2em] ${accent}`}>{eyebrow}</p>
            )}
            <div>
              {title && (
                <h2 className={`${titleClassName ?? 'max-w-[18ch] text-3xl md:text-5xl'} font-semibold leading-[1.08] tracking-[-0.035em]`}>{title}</h2>
              )}
              {intro && <p className={`mt-5 max-w-[58ch] text-base leading-relaxed md:text-lg ${muted}`}>{intro}</p>}
            </div>
          </div>
        )}
        <div data-reveal>{children}</div>
      </div>
    </section>
  )
}
