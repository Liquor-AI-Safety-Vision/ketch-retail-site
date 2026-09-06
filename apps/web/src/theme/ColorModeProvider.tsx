import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';

export type ColorMode = 'light' | 'dark';

interface ColorModeContextValue {
  mode: ColorMode;
  toggleMode: () => void;
}

const storageKey = 'ketch.color-mode';
const ColorModeContext = createContext<ColorModeContextValue | null>(null);

/**
 * The Ketch deployment resolves the stored mode in a pre-paint script so the
 * first frame is already correct. This dashboard is served on its own, so it
 * reads the same key directly and accepts one frame of the default theme.
 */
function initialMode(): ColorMode {
  try {
    return localStorage.getItem(storageKey) === 'dark' ? 'dark' : 'light';
  } catch {
    // The default light theme still works when browser storage is unavailable.
    return 'light';
  }
}

export function ColorModeProvider({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState<ColorMode>(initialMode);

  useEffect(() => {
    document.documentElement.dataset.colorMode = mode;
    document.documentElement.style.colorScheme = mode;
    try {
      localStorage.setItem(storageKey, mode);
    } catch { /* The choice simply does not persist for this visit. */ }
  }, [mode]);

  const value = useMemo<ColorModeContextValue>(() => ({
    mode,
    toggleMode: () => setMode((current) => (current === 'light' ? 'dark' : 'light')),
  }), [mode]);

  return <ColorModeContext.Provider value={value}>{children}</ColorModeContext.Provider>;
}

export function useColorMode() {
  const context = useContext(ColorModeContext);
  if (!context) throw new Error('useColorMode must be used inside ColorModeProvider.');
  return context;
}
