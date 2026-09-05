import type { MouseEvent } from 'react'

/** Smooth-scroll to a section by id, offsetting the 64px sticky nav.
 *  Needed because the app uses HashRouter — raw #anchor links are parsed
 *  as routes and never scroll. Respects prefers-reduced-motion. */
export function scrollToSection(id: string) {
  return (e: MouseEvent<HTMLAnchorElement>) => {
    e.preventDefault()
    const el = document.getElementById(id)
    if (!el) return
    const y = el.getBoundingClientRect().top + window.scrollY - 64
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    window.scrollTo({ top: y, behavior: reduceMotion ? 'auto' : 'smooth' })
  }
}
