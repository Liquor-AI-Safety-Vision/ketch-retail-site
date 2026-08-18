import type { ButtonHTMLAttributes, ReactNode } from 'react'

type Variant = 'primary' | 'secondary' | 'ghost'
type Size = 'sm' | 'md' | 'lg'

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: Size
  children: ReactNode
}

/* §8 button. Primary = deep-teal fill / cream text (AA contrast).
   Labels must stay one line (CTA wrap ban) → keep them ≤3 words. */
const VARIANTS: Record<Variant, string> = {
  primary: 'bg-teal-900 text-cream hover:bg-teal-600 border border-transparent',
  secondary: 'bg-transparent text-teal-900 border border-teal-900/30 hover:border-teal-900/60 hover:bg-teal-900/5',
  ghost: 'bg-transparent text-teal-900 border border-transparent hover:bg-teal-900/5',
}
const SIZES: Record<Size, string> = {
  sm: 'text-sm px-3.5 py-1.5',
  md: 'text-sm px-5 py-2.5',
  lg: 'text-base px-6 py-3',
}

export default function Button({
  variant = 'primary', size = 'md', className = '', children, ...rest
}: ButtonProps) {
  return (
    <button
      className={`inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-lg
        font-semibold tracking-tight transition-colors duration-150
        active:translate-y-px focus-visible:outline-none focus-visible:ring-2
        focus-visible:ring-teal-600 focus-visible:ring-offset-2 focus-visible:ring-offset-cream
        disabled:opacity-50 disabled:pointer-events-none
        ${VARIANTS[variant]} ${SIZES[size]} ${className}`}
      {...rest}
    >
      {children}
    </button>
  )
}
