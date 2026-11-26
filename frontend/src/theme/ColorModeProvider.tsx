import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'

export type ColorMode = 'light' | 'dark'

interface ColorModeContextValue {
  mode: ColorMode
  toggleMode: () => void
}

const storageKey = 'ketch.color-mode'
const ColorModeContext = createContext<ColorModeContextValue | null>(null)

function initialMode(): ColorMode {
  if (typeof document === 'undefined') return 'light'
  return document.documentElement.dataset.colorMode === 'dark' ? 'dark' : 'light'
}

export function ColorModeProvider({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState<ColorMode>(initialMode)

  useEffect(() => {
    document.documentElement.dataset.colorMode = mode
    document.documentElement.style.colorScheme = mode
    localStorage.setItem(storageKey, mode)
  }, [mode])

  const value = useMemo<ColorModeContextValue>(() => ({
    mode,
    toggleMode: () => setMode((current) => current === 'light' ? 'dark' : 'light'),
  }), [mode])

  return <ColorModeContext.Provider value={value}>{children}</ColorModeContext.Provider>
}

export function useColorMode() {
  const context = useContext(ColorModeContext)
  if (!context) throw new Error('useColorMode must be used inside ColorModeProvider.')
  return context
}
