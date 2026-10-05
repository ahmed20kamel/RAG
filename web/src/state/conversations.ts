import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { ChatPicture, ChatResponse } from '@/types/api'

/**
 * Conversation history lives in this browser.
 *
 * The backend has no conversation store and adding one would mean new tables and new
 * business logic, which is outside what this work is allowed to change. Keeping history
 * local is honest about that: it survives reloads on this device and is never implied
 * to be shared or synced.
 */

export type Feedback = 'up' | 'down' | null

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  createdAt: number
  /** A picture the reader attached to this question. */
  picture?: ChatPicture
  /** Present on assistant messages that completed. */
  response?: ChatResponse
  error?: string
  stopped?: boolean
  feedback?: Feedback
}

export interface Conversation {
  id: string
  title: string
  messages: ChatMessage[]
  createdAt: number
  updatedAt: number
  archived: boolean
}

interface ConversationsState {
  conversations: Conversation[]
  activeId: string | null
  create: () => string
  select: (id: string | null) => void
  remove: (id: string) => void
  toggleArchive: (id: string) => void
  rename: (id: string, title: string) => void
  appendMessage: (conversationId: string, message: ChatMessage) => void
  updateMessage: (conversationId: string, messageId: string, patch: Partial<ChatMessage>) => void
  dropMessagesFrom: (conversationId: string, messageId: string) => void
}

export const MAX_TITLE_LENGTH = 60

export function titleFrom(question: string): string {
  const clean = question.replace(/\s+/g, ' ').trim()
  if (clean.length <= MAX_TITLE_LENGTH) return clean
  return `${clean.slice(0, MAX_TITLE_LENGTH).trimEnd()}…`
}

export function newId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID()
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}

export const useConversations = create<ConversationsState>()(
  persist(
    (set) => ({
      conversations: [],
      activeId: null,

      create: () => {
        const id = newId()
        const now = Date.now()
        set((state) => ({
          conversations: [
            { id, title: '', messages: [], createdAt: now, updatedAt: now, archived: false },
            ...state.conversations,
          ],
          activeId: id,
        }))
        return id
      },

      select: (id) => set({ activeId: id }),

      remove: (id) =>
        set((state) => {
          const conversations = state.conversations.filter((c) => c.id !== id)
          const activeId =
            state.activeId === id ? (conversations.find((c) => !c.archived)?.id ?? null) : state.activeId
          return { conversations, activeId }
        }),

      toggleArchive: (id) =>
        set((state) => ({
          conversations: state.conversations.map((c) =>
            c.id === id ? { ...c, archived: !c.archived, updatedAt: Date.now() } : c,
          ),
          activeId: state.activeId === id ? null : state.activeId,
        })),

      rename: (id, title) =>
        set((state) => ({
          conversations: state.conversations.map((c) => (c.id === id ? { ...c, title } : c)),
        })),

      appendMessage: (conversationId, message) =>
        set((state) => ({
          conversations: state.conversations.map((c) => {
            if (c.id !== conversationId) return c
            // The first question names the conversation; later ones leave it alone.
            const title = c.title || (message.role === 'user' ? titleFrom(message.content) : '')
            return { ...c, title, messages: [...c.messages, message], updatedAt: Date.now() }
          }),
        })),

      updateMessage: (conversationId, messageId, patch) =>
        set((state) => ({
          conversations: state.conversations.map((c) =>
            c.id !== conversationId
              ? c
              : {
                  ...c,
                  updatedAt: Date.now(),
                  messages: c.messages.map((m) => (m.id === messageId ? { ...m, ...patch } : m)),
                },
          ),
        })),

      /** Drops a message and everything after it — used when regenerating an answer. */
      dropMessagesFrom: (conversationId, messageId) =>
        set((state) => ({
          conversations: state.conversations.map((c) => {
            if (c.id !== conversationId) return c
            const index = c.messages.findIndex((m) => m.id === messageId)
            if (index === -1) return c
            return { ...c, messages: c.messages.slice(0, index), updatedAt: Date.now() }
          }),
        })),
    }),
    {
      name: 'rag.conversations',
      // Only the list is persisted; which one is open is per-tab and not worth restoring
      // into a window the user may have opened for something else.
      partialize: (state) => ({ conversations: state.conversations }),
    },
  ),
)

export function activeConversation(state: ConversationsState): Conversation | null {
  return state.conversations.find((c) => c.id === state.activeId) ?? null
}
