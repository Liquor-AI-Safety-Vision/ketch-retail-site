import type { HTMLAttributes, ReactNode } from 'react'

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  children: ReactNode
  /** Soft inset surface (cream-200) vs. plain white-on-cream. */
  surface?: 'cream' | 'plain'
}

/* §8 card. Elevation is a hairline border + tinted shadow, not a black drop. */
export default function Card({ children, surface = 'plain', className = '', ...rest }: CardProps) {
  const bg = surface === 'cream' ? 'bg-cream-200' : 'bg-surface'
  return (
    <div
      className={`rounded-xl border border-ink/10 ${bg}
        shadow-[0_1px_3px_rgba(21,33,31,0.06),0_8px_24px_-12px_rgba(21,33,31,0.10)]
        ${className}`}
      {...rest}
    >
      {children}
    </div>
  )
}
