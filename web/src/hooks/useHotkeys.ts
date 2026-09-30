import { useEffect, useRef } from 'react'

export interface Hotkey {
  /** Lower-case `event.key`, e.g. 'k', 'enter', '/'. */
  key: string
  ctrlOrMeta?: boolean
  shift?: boolean
  alt?: boolean
  /** Fire even while a text field has focus. Off by default so typing stays typing. */
  whileTyping?: boolean
  handler: (event: KeyboardEvent) => void
}

const TYPING_TAGS = new Set(['INPUT', 'TEXTAREA', 'SELECT'])

function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  return TYPING_TAGS.has(target.tagName) || target.isContentEditable
}

/**
 * Binds global shortcuts for as long as the component is mounted.
 *
 * The handlers are held in a ref so a shortcut does not have to be re-registered on
 * every render — otherwise a key pressed mid-render could reach a stale closure.
 */
export function useHotkeys(hotkeys: Hotkey[]): void {
  const current = useRef(hotkeys)
  current.current = hotkeys

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      const typing = isTyping(event.target)
      for (const hotkey of current.current) {
        if (event.key.toLowerCase() !== hotkey.key.toLowerCase()) continue
        if (!!hotkey.ctrlOrMeta !== (event.ctrlKey || event.metaKey)) continue
        if (!!hotkey.shift !== event.shiftKey) continue
        if (!!hotkey.alt !== event.altKey) continue
        if (typing && !hotkey.whileTyping) continue
        event.preventDefault()
        hotkey.handler(event)
        return
      }
    }

    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [])
}

/** "Ctrl" on Windows and Linux, "⌘" on a Mac — shown in shortcut hints. */
export function modifierLabel(): string {
  if (typeof navigator === 'undefined') return 'Ctrl'
  return /mac|iphone|ipad/i.test(navigator.platform || navigator.userAgent) ? '⌘' : 'Ctrl'
}
