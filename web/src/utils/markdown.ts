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

const CITATION = /\[(\d{1,2})\]/g

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

  const marked_source = source.replace(CITATION, (match, digits: string) => {
    const n = Number(digits)
    if (!Number.isFinite(n) || n < 1) return match
    if (!citations.includes(n)) citations.push(n)
    return `${OPEN}${n}${CLOSE}`
  })

  const parsed = marked.parse(marked_source, { async: false }) as string

  const withCitations = parsed.replace(
    new RegExp(`${OPEN}(\\d{1,2})${CLOSE}`, 'g'),
    (_match, digits: string) =>
      `<button type="button" class="citation-chip" data-citation="${digits}" aria-label="citation ${digits}">${digits}</button>`,
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
