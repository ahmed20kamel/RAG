import { useCallback, useMemo } from 'react'
import { DICTIONARIES, DIRECTION, interpolate, type Dictionary, type Direction, type Language } from '@/i18n'
import { usePreferences } from '@/state/preferences'

type Leaves<T> = T extends string
  ? never
  : { [K in keyof T & string]: T[K] extends string ? K : `${K}.${Leaves<T[K]>}` }[keyof T & string]

export type TranslationKey = Leaves<Dictionary>

function lookup(dictionary: Dictionary, key: string): string {
  const value = key
    .split('.')
    .reduce<unknown>((node, part) => (node as Record<string, unknown>)?.[part], dictionary)
  // A missing key shows as the key itself rather than an empty space, so it is obvious
  // in review instead of silently blank in production.
  return typeof value === 'string' ? value : key
}

export interface Translation {
  t: (key: TranslationKey, values?: Record<string, string | number>) => string
  language: Language
  direction: Direction
  isRtl: boolean
}

export function useTranslation(): Translation {
  const language = usePreferences((state) => state.language)
  const dictionary = DICTIONARIES[language]

  const t = useCallback(
    (key: TranslationKey, values?: Record<string, string | number>) =>
      interpolate(lookup(dictionary, key), values),
    [dictionary],
  )

  return useMemo(
    () => ({ t, language, direction: DIRECTION[language], isRtl: language === 'ar' }),
    [t, language],
  )
}
