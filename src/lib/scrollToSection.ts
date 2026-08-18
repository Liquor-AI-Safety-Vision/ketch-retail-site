import type { MouseEvent } from 'react'

/** Smooth-scroll to a section by id, offsetting the 56px sticky nav.
 *  Needed because the app uses HashRouter — raw #anchor links are parsed
 *  as routes and never scroll. */
export function scrollToSection(id: string) {
  return (e: MouseEvent<HTMLAnchorElement>) => {
    e.preventDefault()
    const el = document.getElementById(id)
    if (!el) return
    const y = el.getBoundingClientRect().top + window.scrollY - 64
    window.scrollTo({ top: y, behavior: 'smooth' })
  }
}
