import { describe, expect, it } from 'vitest'
import { breadcrumbParts, fileExtension, formatBytes, formatNumber, formatPercent } from './format'

describe('formatNumber', () => {
  it('uses Western digits in Arabic, because the documents state values that way', () => {
    expect(formatNumber(268000, 'ar')).toMatch(/268/)
    expect(formatNumber(268000, 'ar')).not.toMatch(/[٠-٩]/)
  })
})

describe('formatBytes', () => {
  it('scales to the right unit', () => {
    expect(formatBytes(0, 'en')).toBe('0 KB')
    expect(formatBytes(512, 'en')).toBe('512 B')
    expect(formatBytes(2048, 'en')).toBe('2 KB')
    expect(formatBytes(5 * 1024 * 1024, 'en')).toBe('5 MB')
  })
})

describe('formatPercent', () => {
  it('rounds a ratio to whole percent', () => {
    expect(formatPercent(0.985, 'en')).toBe('99%')
    expect(formatPercent(1, 'en')).toBe('100%')
  })
})

describe('fileExtension', () => {
  it('returns the lower-cased extension, or nothing when there is none', () => {
    expect(fileExtension('CONTRACT.MD')).toBe('.md')
    expect(fileExtension('readme')).toBe('')
  })
})

describe('breadcrumbParts', () => {
  it('splits a stored section path and drops the padding', () => {
    expect(breadcrumbParts('المستند → القسم → البند')).toEqual(['المستند', 'القسم', 'البند'])
    expect(breadcrumbParts('')).toEqual([])
  })
})
