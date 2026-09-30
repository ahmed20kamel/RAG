import { describe, expect, it } from 'vitest'
import { renderMarkdown, markdownToPlainText } from './markdown'

describe('renderMarkdown', () => {
  it('turns a citation into a button carrying its number', () => {
    const { html, citations } = renderMarkdown('المهندس سالم وقّع العقد [1].')
    expect(citations).toEqual([1])
    expect(html).toContain('data-citation="1"')
    expect(html).toContain('citation-chip')
  })

  it('lists every distinct citation once, in order of first appearance', () => {
    const { citations } = renderMarkdown('أولًا [2]، ثم [1]، ثم [2] مرة أخرى.')
    expect(citations).toEqual([2, 1])
  })

  it('renders GitHub tables, which the documents rely on', () => {
    const { html } = renderMarkdown('| البند | القيمة |\n|---|---|\n| رقم | 134 |')
    expect(html).toContain('<table>')
    expect(html).toContain('134')
  })

  it('keeps code fences intact', () => {
    const { html } = renderMarkdown('```\nconst x = 1\n```')
    expect(html).toContain('<pre>')
    expect(html).toContain('const x = 1')
  })

  it('strips script markup rather than rendering it', () => {
    const { html } = renderMarkdown('<script>window.stolen = 1</script>ok')
    expect(html).not.toContain('<script')
    expect(html).toContain('ok')
  })

  it('leaves a bracketed number that is not a citation alone when it is not numeric', () => {
    const { citations } = renderMarkdown('البند [أ] لا يحمل رقمًا.')
    expect(citations).toEqual([])
  })
})

describe('markdownToPlainText', () => {
  it('drops emphasis markers so a copied answer reads as prose', () => {
    expect(markdownToPlainText('**رقم** الرخصة `B1N`')).toBe('رقم الرخصة B1N')
  })

  it('turns list markers into bullets', () => {
    expect(markdownToPlainText('- أولًا\n- ثانيًا')).toBe('• أولًا\n• ثانيًا')
  })
})
