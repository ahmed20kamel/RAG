import { api, streamNdjson } from './client'
import type { ChatRequest, ChatResponse, ChatStreamEvent } from '@/types/api'

export const chatApi = {
  /** One-shot answer. Used when the stage stream is unavailable. */
  ask: (body: ChatRequest, signal?: AbortSignal) =>
    api.post<ChatResponse>('/api/chat', body, { signal }),

  /**
   * Asks with live stage events. The model is not streamed — the pipeline validates an
   * answer before returning it, so there is no partial text to show — but each stage
   * event fires when that stage actually runs.
   */
  askStreaming: (
    body: ChatRequest,
    onEvent: (event: ChatStreamEvent) => void,
    signal?: AbortSignal,
  ) => streamNdjson<ChatStreamEvent>('/api/chat/stream', body, onEvent, signal),

  /** The reader's thumbs on an answer; a thumbs-down answer is never repeated. */
  feedback: (answerId: string, feedback: 'up' | 'down' | null) =>
    api.post<{ recorded: boolean }>('/api/chat/feedback', { answer_id: answerId, feedback: feedback ?? '' }),
}
