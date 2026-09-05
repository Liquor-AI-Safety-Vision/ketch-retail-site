export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'onBand'
export type ButtonSize = 'sm' | 'md' | 'lg'

/* §8 button. Primary = deep-teal fill / cream text (AA contrast).
   Labels must stay one line (CTA wrap ban) → keep them ≤3 words.

   Each variant carries its own focus ring and ring offset, because the offset
   has to match the surface the button actually sits on.

   `onBand` exists because in light mode --ketch-teal-900 and --ketch-band are
   the same value (#0E5C5B): a primary button placed on a deep-teal band is
   invisible. This variant inverts the fill so a band CTA reads in both modes. */
const VARIANTS: Record<ButtonVariant, string> = {
  primary: 'bg-teal-900 text-cream hover:bg-teal-700 border border-transparent focus-visible:ring-teal-600 focus-visible:ring-offset-cream',
  secondary: 'bg-transparent text-teal-900 border border-teal-900/30 hover:border-teal-900/60 hover:bg-teal-900/5 focus-visible:ring-teal-600 focus-visible:ring-offset-cream',
  ghost: 'bg-transparent text-teal-900 border border-transparent hover:bg-teal-900/5 focus-visible:ring-teal-600 focus-visible:ring-offset-cream',
  onBand: 'bg-band-text text-band hover:bg-white border border-transparent focus-visible:ring-band-accent focus-visible:ring-offset-band',
}
const SIZES: Record<ButtonSize, string> = {
  sm: 'min-h-10 text-sm px-3.5 py-1.5',
  md: 'min-h-11 text-sm px-5 py-2.5',
  lg: 'min-h-12 text-base px-6 py-3',
}

/** Shared visual composition so Button and LinkButton render identically
 *  while each stays a single semantic interactive element. */
export function buttonClasses(variant: ButtonVariant = 'primary', size: ButtonSize = 'md', className = '') {
  return `inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md
    font-semibold tracking-[-0.01em] transition-[background-color,border-color,color,transform] duration-200
    active:translate-y-px focus-visible:outline-none focus-visible:ring-2
    focus-visible:ring-offset-2
    disabled:opacity-50 disabled:pointer-events-none
    ${VARIANTS[variant]} ${SIZES[size]} ${className}`
}
