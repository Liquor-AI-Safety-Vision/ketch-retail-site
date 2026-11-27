import { useEffect } from 'react'

/**
 * Minimal head manager for the two static landing sites — no react-helmet.
 * Sets <title>, upserts the description + Open Graph tags, and injects one
 * JSON-LD block so the page presents cleanly when Christ shares the link
 * (LinkedIn/Zalo/search). HashRouter means every route shares one document
 * head; each landing page calls this once on mount.
 */

interface Seo {
  title: string
  description: string
  /** schema.org object rendered as application/ld+json */
  jsonLd?: Record<string, unknown>
  /** document language for the <html lang> attribute */
  lang?: string
}

function upsertMeta(attr: 'name' | 'property', key: string, content: string) {
  let el = document.head.querySelector<HTMLMetaElement>(`meta[${attr}="${key}"]`)
  if (!el) {
    el = document.createElement('meta')
    el.setAttribute(attr, key)
    document.head.appendChild(el)
  }
  el.setAttribute('content', content)
}

export function useSeo({ title, description, jsonLd, lang }: Seo) {
  useEffect(() => {
    document.title = title
    if (lang) document.documentElement.lang = lang
    upsertMeta('name', 'description', description)
    upsertMeta('property', 'og:title', title)
    upsertMeta('property', 'og:description', description)
    upsertMeta('property', 'og:type', 'website')

    let script: HTMLScriptElement | null = null
    if (jsonLd) {
      script = document.createElement('script')
      script.type = 'application/ld+json'
      script.textContent = JSON.stringify(jsonLd)
      document.head.appendChild(script)
    }
    return () => {
      if (script) document.head.removeChild(script)
    }
  }, [title, description, jsonLd, lang])
}
