import { useEffect, useState } from 'react'

export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() =>
    typeof window === 'undefined' ? false : window.matchMedia(query).matches,
  )

  useEffect(() => {
    const list = window.matchMedia(query)
    const onChange = (event: MediaQueryListEvent) => setMatches(event.matches)
    setMatches(list.matches)
    list.addEventListener('change', onChange)
    return () => list.removeEventListener('change', onChange)
  }, [query])

  return matches
}

/** Below this the navigation becomes a drawer and multi-column layouts stack. */
export const MOBILE_QUERY = '(max-width: 47.99rem)'
export const TABLET_QUERY = '(max-width: 63.99rem)'

export const useIsMobile = () => useMediaQuery(MOBILE_QUERY)
export const useIsTablet = () => useMediaQuery(TABLET_QUERY)
