import { createContext, useContext } from 'react'

/* Lean two-language support. This site is English-only, so nothing wraps a
   provider and the context default below is what every consumer sees. Kept
   because the shared console components call useLang(); the VI strings they
   reach for live in consoleStrings.ts and are unused here. */

export type Lang = 'vi' | 'en'

export const LANG_STORAGE_KEY = 'ketch-lang'

export interface LangState {
  lang: Lang
  setLang: (l: Lang) => void
}

// Default 'en', and nothing overrides it — there is no provider in this repo.
export const LangContext = createContext<LangState>({ lang: 'en', setLang: () => {} })

export function useLang(): LangState {
  return useContext(LangContext)
}

/**
 * Pick a localized string for demo data that carries an optional English
 * variant. VI keeps the base (authored in Vietnamese); EN uses the `_en`
 * field when present, else falls back to the base. Retail clips have no `_en`
 * and are already English, so the fallback leaves them untouched.
 */
export function pick(lang: Lang, base: string, en?: string | null): string {
  return lang === 'en' && en ? en : base
}
