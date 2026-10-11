import { Moon, Sun } from 'lucide-react'
import { useState } from 'react'
import { IconButton } from './ui.tsx'

/** Switches between the default light theme and dark, remembering the choice in this browser. */
export function ThemeToggle() {
  const [dark, setDark] = useState(() => document.documentElement.dataset.theme === 'dark')

  function toggle() {
    const next = !dark
    setDark(next)
    if (next) document.documentElement.dataset.theme = 'dark'
    else delete document.documentElement.dataset.theme
    try {
      localStorage.setItem('theme', next ? 'dark' : 'light')
    } catch {
      // Storage can be blocked; the theme still applies for this visit.
    }
  }

  return (
    <IconButton label={dark ? 'Switch to light theme' : 'Switch to dark theme'} onClick={toggle}>
      {dark ? <Sun className="size-4" /> : <Moon className="size-4" />}
    </IconButton>
  )
}
