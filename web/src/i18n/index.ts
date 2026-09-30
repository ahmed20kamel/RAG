import { ar, type Dictionary } from './ar'
import { en } from './en'

export type Language = 'ar' | 'en'
export type Direction = 'rtl' | 'ltr'

export const DICTIONARIES: Record<Language, Dictionary> = { ar, en }
export const DIRECTION: Record<Language, Direction> = { ar: 'rtl', en: 'ltr' }

export type { Dictionary }

/**
 * Fills `{{name}}` placeholders. Kept deliberately small: the strings here are UI
 * labels, not prose with plural rules, so a full i18n runtime would cost more than
 * it returns.
 */
export function interpolate(template: string, values?: Record<string, string | number>): string {
  if (!values) return template
  return template.replace(/\{\{(\w+)\}\}/g, (match, key: string) =>
    key in values ? String(values[key]) : match,
  )
}
