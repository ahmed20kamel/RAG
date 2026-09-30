import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * Copies text and reports success for a moment, so a button can confirm itself.
 *
 * Two paths, because the modern one does not exist where this app actually runs.
 * `navigator.clipboard` is gated behind a secure context: browsers expose it over
 * HTTPS and on localhost, and nowhere else. This app is served to the office over
 * plain HTTP at a LAN address, so for every person using it the property is simply
 * `undefined` — every copy button threw, and the UI reported "an unexpected error"
 * for something that was neither unexpected nor an error.
 *
 * So the old `execCommand` path is kept as the fallback. It is deprecated and it is
 * also the only thing that works here, which settles the argument: a deprecated API
 * that copies beats a modern one that is absent.
 */
export function useCopy(resetAfter = 1800): { copied: boolean; copy: (text: string) => Promise<boolean> } {
  const [copied, setCopied] = useState(false)
  const timer = useRef<number>()

  useEffect(() => () => window.clearTimeout(timer.current), [])

  const copy = useCallback(
    async (text: string) => {
      const ok = (await writeToClipboard(text)) || copyViaSelection(text)
      if (!ok) return false

      setCopied(true)
      window.clearTimeout(timer.current)
      timer.current = window.setTimeout(() => setCopied(false), resetAfter)
      return true
    },
    [resetAfter],
  )

  return { copied, copy }
}

/** The modern path. Absent outside a secure context, and refused when permission is denied. */
async function writeToClipboard(text: string): Promise<boolean> {
  if (!navigator.clipboard?.writeText) return false
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    return false
  }
}

/**
 * The fallback: select text in an offscreen textarea and let the browser copy it.
 *
 * Positioned rather than hidden, because `display:none` and `visibility:hidden`
 * elements cannot be selected and the copy silently does nothing. Kept at the
 * current scroll offset so focusing it does not jump the page, and RTL-safe by
 * sitting outside the viewport on the inline axis rather than a fixed side.
 */
function copyViaSelection(text: string): boolean {
  const area = document.createElement('textarea')
  area.value = text
  area.setAttribute('readonly', '')
  area.setAttribute('aria-hidden', 'true')
  area.style.position = 'fixed'
  area.style.top = `${window.scrollY}px`
  area.style.insetInlineStart = '-9999px'
  area.style.opacity = '0'

  document.body.appendChild(area)
  const previous = document.activeElement as HTMLElement | null
  try {
    area.focus({ preventScroll: true })
    area.select()
    area.setSelectionRange(0, text.length)
    return document.execCommand('copy')
  } catch {
    return false
  } finally {
    document.body.removeChild(area)
    previous?.focus?.()
  }
}
