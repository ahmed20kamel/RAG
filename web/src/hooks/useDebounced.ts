import { useEffect, useState } from 'react'

/** The value after it has stopped changing for `delay` — used to keep search off every keystroke. */
export function useDebounced<T>(value: T, delay = 300): T {
  const [settled, setSettled] = useState(value)

  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(value), delay)
    return () => window.clearTimeout(timer)
  }, [value, delay])

  return settled
}
