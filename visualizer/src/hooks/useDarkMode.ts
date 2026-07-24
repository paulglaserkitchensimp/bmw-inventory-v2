import { useEffect, useState } from 'react'

const STORAGE_KEY = 'bmw-viz-theme'

/**
 * Dark-mode state: persisted in localStorage, defaults to the OS preference.
 * Toggles the `dark` class on <html>, which drives the `.dark` override layer
 * in index.css (and any future Tailwind `dark:` variants).
 */
export function useDarkMode() {
  const [dark, setDark] = useState<boolean>(() => {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored === 'dark') return true
    if (stored === 'light') return false
    return window.matchMedia('(prefers-color-scheme: dark)').matches
  })

  useEffect(() => {
    document.documentElement.classList.toggle('dark', dark)
    localStorage.setItem(STORAGE_KEY, dark ? 'dark' : 'light')
  }, [dark])

  return { dark, toggleDark: () => setDark(d => !d) }
}
