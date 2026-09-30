import type { Language } from '@/i18n'

const LOCALE: Record<Language, string> = { ar: 'ar-AE', en: 'en-GB' }

/**
 * Numbers are formatted with Western digits in both languages: the documents state
 * identifiers, licence numbers and amounts in Western digits, and an answer that shows
 * "١٣٤" beside a source reading "134" reads as a different value.
 */
export function formatNumber(value: number, language: Language): string {
  return new Intl.NumberFormat(`${LOCALE[language]}-u-nu-latn`).format(value)
}

export function formatBytes(bytes: number, language: Language): string {
  if (!bytes) return '0 KB'
  const units = ['B', 'KB', 'MB', 'GB']
  const exponent = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
  const value = bytes / 1024 ** exponent
  const rounded = value >= 100 || exponent === 0 ? Math.round(value) : Math.round(value * 10) / 10
  return `${formatNumber(rounded, language)} ${units[exponent]}`
}

export function formatDate(iso: string | null | undefined, language: Language): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat(`${LOCALE[language]}-u-nu-latn`, {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
  }).format(date)
}

export function formatDateTime(iso: string | null | undefined, language: Language): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat(`${LOCALE[language]}-u-nu-latn`, {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

/** "3 minutes ago" — used where the exact timestamp matters less than the recency. */
export function formatRelative(value: number | string | null | undefined, language: Language): string {
  if (value === null || value === undefined) return '—'
  const time = typeof value === 'number' ? value : new Date(value).getTime()
  if (Number.isNaN(time)) return '—'

  const seconds = Math.round((time - Date.now()) / 1000)
  const formatter = new Intl.RelativeTimeFormat(LOCALE[language], { numeric: 'auto' })
  const divisions: [number, Intl.RelativeTimeFormatUnit][] = [
    [60, 'second'],
    [60, 'minute'],
    [24, 'hour'],
    [7, 'day'],
    [4.35, 'week'],
    [12, 'month'],
    [Number.POSITIVE_INFINITY, 'year'],
  ]

  let amount = seconds
  for (const [span, unit] of divisions) {
    if (Math.abs(amount) < span) return formatter.format(Math.round(amount), unit)
    amount /= span
  }
  return formatter.format(Math.round(amount), 'year')
}

export function formatDuration(ms: number, language: Language): string {
  if (ms < 1000) return `${formatNumber(Math.round(ms), language)} ms`
  const seconds = ms / 1000
  return `${formatNumber(Math.round(seconds * 10) / 10, language)} s`
}

export function formatPercent(ratio: number, language: Language): string {
  return `${formatNumber(Math.round(ratio * 100), language)}%`
}

export function fileExtension(filename: string): string {
  const dot = filename.lastIndexOf('.')
  return dot === -1 ? '' : filename.slice(dot).toLowerCase()
}

/** Splits a stored breadcrumb ("A → B → C") into its parts. */
export function breadcrumbParts(path: string): string[] {
  return path
    .split('→')
    .map((part) => part.trim())
    .filter(Boolean)
}
