import { marked } from 'marked'
import DOMPurify from 'dompurify'

/**
 * Renders an answer to sanitised HTML and makes its citations clickable.
 *
 * Citations arrive inside the model's prose as `[1]`. They are turned into buttons
 * carrying the citation number, which the message component listens for, so opening a
 * source needs no per-citation React node and no re-render of the answer body.
 */

marked.setOptions({ gfm: true, breaks: true })

/** One or more adjacent citations: "[1]", "[1][3]", "[1] [3]", "[1, 3]", "[1، 3]". */
const CITATION_RUN = /\[\d{1,2}(?:\s*[,،]\s*\d{1,2})*\](?:\s*\[\d{1,2}(?:\s*[,،]\s*\d{1,2})*\])*/g

export interface RenderedMarkdown {
  html: string
  /** Citation numbers referenced by the text, in the order they first appear. */
  citations: number[]
}

/** Placeholders keep citation markup out of the Markdown parser's way. */
const OPEN = 'CITE'
const CLOSE = 'CITE'

export function renderMarkdown(source: string): RenderedMarkdown {
  const citations: number[] = []

  // A run of citations — "[1][3]", "[1] [3]", "[1, 3]" — becomes one marker. A row of
  // numbers after every sentence read as clutter; one small mark per claim is enough,
  // and every source it stands for is still listed and opens from it.
  const marked_source = source.replace(CITATION_RUN, (match: string) => {
    const numbers: number[] = []
    for (const found of match.matchAll(/\d{1,2}/g)) {
      const n = Number(found[0])
      if (Number.isFinite(n) && n >= 1 && !numbers.includes(n)) numbers.push(n)
    }
    if (numbers.length === 0) return match
    for (const n of numbers) if (!citations.includes(n)) citations.push(n)
    return `${OPEN}${numbers.join('-')}${CLOSE}`
  })

  const parsed = marked.parse(marked_source, { async: false }) as string

  const withCitations = parsed.replace(
    new RegExp(`${OPEN}([\\d-]{1,40})${CLOSE}`, 'g'),
    (_match, group: string) => {
      const numbers = group.split('-')
      const first = numbers[0]
      const label = numbers.length > 1 ? `${first}+${numbers.length - 1}` : first
      return `<button type="button" class="citation-chip" data-citation="${first}" title="${numbers.join('، ')}" aria-label="citation ${numbers.join(', ')}">${label}</button>`
    },
  )

  const html = DOMPurify.sanitize(withCitations, {
    ADD_ATTR: ['data-citation', 'target', 'rel'],
    FORBID_TAGS: ['style', 'form', 'input'],
    FORBID_ATTR: ['style', 'onerror', 'onload'],
  })

  return { html, citations }
}

/** Plain text for the clipboard: the answer without its rendered markup. */
export function markdownToPlainText(source: string): string {
  return source
    .replace(/```[\s\S]*?```/g, (block) => block.replace(/```\w*\n?/g, ''))
    .replace(/[*_~`]/g, '')
    .replace(/^\s*[-*+]\s+/gm, '• ')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}
