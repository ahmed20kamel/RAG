import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { DIRECTION, type Language } from '@/i18n'

export type ThemeChoice = 'light' | 'dark' | 'system'

interface PreferencesState {
  theme: ThemeChoice
  language: Language
  railCollapsed: boolean
  setTheme: (theme: ThemeChoice) => void
  cycleTheme: () => void
  setLanguage: (language: Language) => void
  toggleRail: () => void
}

/** Detects the document language once, so an Arabic user is not greeted in English. */
function initialLanguage(): Language {
  if (typeof navigator === 'undefined') return 'ar'
  return navigator.language?.toLowerCase().startsWith('en') ? 'en' : 'ar'
}

export const usePreferences = create<PreferencesState>()(
  persist(
    (set, get) => ({
      theme: 'system',
      language: initialLanguage(),
      railCollapsed: false,
      setTheme: (theme) => set({ theme }),
      cycleTheme: () => {
        const order: ThemeChoice[] = ['light', 'dark', 'system']
        const next = order[(order.indexOf(get().theme) + 1) % order.length]
        set({ theme: next })
      },
      setLanguage: (language) => set({ language }),
      toggleRail: () => set({ railCollapsed: !get().railCollapsed }),
    }),
    { name: 'rag.preferences' },
  ),
)

/**
 * Writes the chosen theme and language onto the document element.
 *
 * `data-theme` is set only for an explicit choice: leaving it off under "system" lets
 * the `prefers-color-scheme` block in tokens.css decide, which is what keeps the page
 * following the OS without any JavaScript listening for changes.
 */
export function applyPreferences(theme: ThemeChoice, language: Language): void {
  const root = document.documentElement
  if (theme === 'system') root.removeAttribute('data-theme')
  else root.setAttribute('data-theme', theme)

  root.setAttribute('lang', language)
  root.setAttribute('dir', DIRECTION[language])
}
