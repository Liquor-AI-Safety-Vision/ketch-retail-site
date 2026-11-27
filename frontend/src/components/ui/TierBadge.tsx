import type { Tier } from '../../types'
import { TIER_META } from '../../types'
import { useLang } from '../../lib/i18n'
import { CONSOLE } from '../../lib/consoleStrings'

interface TierBadgeProps {
  tier: Tier
  /** 'solid' = filled pill (on video / dark); 'soft' = tinted chip (on cream). */
  variant?: 'solid' | 'soft'
  className?: string
}

/* The only place tier → color/name is rendered. Keeps the §8 functional
   accents (amber early-warning, red priority) consistent everywhere. */
export default function TierBadge({ tier, variant = 'soft', className = '' }: TierBadgeProps) {
  const { lang } = useLang()
  const m = TIER_META[tier]
  const name = CONSOLE[lang].tier[tier]
  if (variant === 'solid') {
    return (
      <span className={`inline-flex items-center gap-1.5 rounded-md ${m.bg} text-white
        text-[11px] font-bold uppercase tracking-wide px-2 py-0.5 ${className}`}>
        {name}
      </span>
    )
  }
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-md ${m.chip} ${m.text}
      text-[11px] font-bold uppercase tracking-wide px-2 py-0.5 ${className}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${m.dot}`} />
      {name}
    </span>
  )
}
