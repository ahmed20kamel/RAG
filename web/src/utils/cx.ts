/** Joins class names, dropping anything falsy, so conditionals read inline. */
export function cx(...values: Array<string | false | null | undefined>): string {
  return values.filter(Boolean).join(' ')
}
