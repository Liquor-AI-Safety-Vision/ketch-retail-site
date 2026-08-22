import { Moon, Sun } from '@phosphor-icons/react'
import { useColorMode } from '../theme/ColorModeProvider'

interface ThemeToggleProps {
  className?: string
  compact?: boolean
}

export default function ThemeToggle({ className = '', compact = false }: ThemeToggleProps) {
  const { mode, toggleMode } = useColorMode()
  const nextMode = mode === 'light' ? 'dark' : 'light'

  return (
    <button
      type="button"
      onClick={toggleMode}
      aria-label={`Switch to ${nextMode} mode`}
      title={`Switch to ${nextMode} mode`}
      className={`group inline-flex h-9 items-center justify-center gap-2 rounded-lg border border-ink/15 bg-cream-200 text-ink shadow-sm transition-[background-color,border-color,color,transform] duration-200 hover:-translate-y-px hover:border-teal-600/50 hover:bg-cream focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-600 focus-visible:ring-offset-2 focus-visible:ring-offset-cream ${compact ? 'w-9 px-0' : 'px-3'} ${className}`}
    >
      {mode === 'light' ? <Moon size={16} weight="bold" /> : <Sun size={16} weight="bold" />}
      {!compact && <span className="text-xs font-semibold">{mode === 'light' ? 'Dark' : 'Light'}</span>}
    </button>
  )
}
