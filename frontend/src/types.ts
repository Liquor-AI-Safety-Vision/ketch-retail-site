export type Tier = 1 | 2 | 3
export type Mode = 'store' | 'factory'

export interface TierStyle {
  name: string
  bg: string      // solid fill (badge background)
  text: string    // accent text
  border: string  // hairline border
  dot: string     // status dot
  ring: string    // focus/active ring
  chip: string    // soft tinted chip background (on cream)
}

/* Triage-honest tier semantics (scoping-spec §5 — never a "theft" verdict):
   1 = Priority Alert (red, the headline walkout)
   2 = Early Warning  (amber, dwell/suspicious)
   3 = Zone Breach    (teal). Colors map to §8 tokens in index.css. */
export const TIER_META: Record<Tier, TierStyle> = {
  1: { name: 'Priority Alert', bg: 'bg-red',      text: 'text-red-text',      border: 'border-red/40',      dot: 'bg-red-text',      ring: 'ring-red',      chip: 'bg-red-bg' },
  2: { name: 'Early Warning',  bg: 'bg-amber',     text: 'text-amber-text',    border: 'border-amber/40',    dot: 'bg-amber-text',    ring: 'ring-amber',    chip: 'bg-amber-bg' },
  3: { name: 'Zone Breach',    bg: 'bg-teal-600',  text: 'text-teal-600', border: 'border-teal-600/40', dot: 'bg-teal-600', ring: 'ring-teal-600', chip: 'bg-cream-200' },
}
