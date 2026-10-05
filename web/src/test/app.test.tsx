import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { App } from '@/App'
import type { CurrentUser } from '@/types/auth'

/**
 * Boots the whole application against a stubbed API.
 *
 * Type-checking proves the pieces fit; this proves they run — that the router mounts,
 * the auth gate decides, a lazy route resolves, the theme and direction reach `<html>`,
 * and no effect throws on the way.
 */

const user: CurrentUser = {
  id: 'u1',
  email: 'tester@local',
  display_name: 'Tester',
  role: 'admin',
  team_id: null,
  is_active: true,
  created_at: '2026-01-01T00:00:00Z',
  last_login_at: null,
  permissions: ['chat.ask', 'document.read', 'knowledge.read', 'system.read', 'user.manage'],
  team_name: '',
}

const health = {
  status: 'ok',
  ollama: { reachable: true, url: 'http://x:11434', model: 'm', model_available: true },
  embeddings: { reachable: true, url: 'http://x:11434', model: 'e', model_available: true },
  qdrant: { reachable: true, url: 'http://x:6333', collection: 'c', collection_exists: true, points: 5 },
  keyword_index: { chunks: 5, terms: 20 },
  knowledge_index: { sections: 4, entities: 6, chunks: 5 },
  embedding_cache: { hits: 0, misses: 0 },
}

const stats = {
  documents: 2,
  by_status: { completed: 2 },
  processing: 0,
  failed: 0,
  chunks: 5,
  characters: 100,
  bytes: 200,
  categories: 1,
  last_ingested_at: null,
  last_uploaded_at: null,
}

/** `signedIn: false` makes /api/auth/me answer 401, which is how the gate sees a guest. */
function stubApi({ signedIn = true }: { signedIn?: boolean } = {}) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)

      if (url.includes('/api/auth/me')) {
        return signedIn
          ? new Response(JSON.stringify(user), {
              status: 200,
              headers: { 'Content-Type': 'application/json' },
            })
          : new Response(JSON.stringify({ detail: 'unauthenticated' }), {
              status: 401,
              headers: { 'Content-Type': 'application/json' },
            })
      }

      const body = url.includes('/api/health')
        ? health
        : url.includes('/api/documents/stats')
          ? stats
          : url.includes('/api/config')
            ? { supported_extensions: ['.md'] }
            : { total: 0, items: [] }

      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }),
  )
}

beforeEach(() => {
  window.localStorage.clear()
  window.history.pushState({}, '', '/')
  stubApi()
})

describe('App', () => {
  it('mounts and lands on the chat route for a signed-in user', async () => {
    render(<App />)
    expect(await screen.findByRole('navigation')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByPlaceholderText(/اسأل|Ask/)).toBeInTheDocument())
  })

  it('applies the stored language and direction to the document element', async () => {
    render(<App />)
    await waitFor(() => {
      expect(document.documentElement.getAttribute('dir')).toMatch(/rtl|ltr/)
      expect(document.documentElement.getAttribute('lang')).toMatch(/ar|en/)
    })
  })

  it('shows the welcome state when no conversation has been started', async () => {
    render(<App />)
    expect(await screen.findByText(/كيف أقدر أساعدك|How can I help/)).toBeInTheDocument()
    expect(screen.getByText(/القيم والمبالغ|Values and amounts/)).toBeInTheDocument()
  })

  it('sends a signed-out visitor to the login page instead of the app', async () => {
    stubApi({ signedIn: false })
    render(<App />)
    // The sign-in button proves the login route rendered; the composer would prove the
    // gate had let a guest through.
    expect(await screen.findByRole('button', { name: /تسجيل الدخول|Sign in/ })).toBeInTheDocument()
    expect(screen.queryByPlaceholderText(/اسأل|Ask/)).not.toBeInTheDocument()
  })

  it('offers user administration only to a role that carries it', async () => {
    render(<App />)
    expect(await screen.findByRole('navigation')).toBeInTheDocument()
    await waitFor(() =>
      expect(screen.getByRole('link', { name: /المستخدمون|Users/ })).toBeInTheDocument(),
    )
  })

  it('hides user administration from a role that does not carry it', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        const body = url.includes('/api/auth/me')
          ? { ...user, role: 'viewer', permissions: ['chat.ask', 'document.read'] }
          : url.includes('/api/health')
            ? health
            : url.includes('/api/documents/stats')
              ? stats
              : { total: 0, items: [] }
        return new Response(JSON.stringify(body), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }),
    )
    render(<App />)
    expect(await screen.findByRole('navigation')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByPlaceholderText(/اسأل|Ask/)).toBeInTheDocument())
    expect(screen.queryByRole('link', { name: /المستخدمون|Users/ })).not.toBeInTheDocument()
  })
})
