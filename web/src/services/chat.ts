import { API_BASE, ApiError, api, streamNdjson } from './client'
import type { ChatFile, ChatRequest, ChatResponse, ChatStreamEvent, PictureMark } from '@/types/api'

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

  /** Reads a picture and the parts marked on it; only the words come back. */
  readPicture: (file: Blob, marks: PictureMark[]) => {
    const form = new FormData()
    form.append('file', file, 'picture.jpg')
    form.append('marks', JSON.stringify(marks))
    return api.post<{ text: string; marked: string; vision?: string; has_text: boolean }>('/api/chat/picture', form)
  },

  /** Speech to text on the server; the text returns to the input box for review. */
  transcribe: (audio: Blob) => {
    const form = new FormData()
    const extension = audio.type.includes('ogg') ? 'ogg' : audio.type.includes('mp4') ? 'mp4' : 'webm'
    form.append('file', audio, `speech.${extension}`)
    return api.post<{ text: string }>('/api/chat/transcribe', form)
  },

  /** An answer on the screen, made into a branded file. */
  exportAnswer: (body: { format: 'pdf' | 'docx' | 'xlsx'; title: string; markdown: string; sources: unknown[] }) =>
    api.post<{ files: ChatFile[]; note: string }>('/api/chat/export', body),

  /** Downloads a file made for this reader, under the name it was made with. */
  downloadFile: async (file: ChatFile) => {
    const response = await fetch(`${API_BASE}/api/chat/files/${file.id}`, { credentials: 'include' })
    if (!response.ok) throw new ApiError(response.status === 404 ? 'الملف غير موجود أو انتهت مدته (7 أيام).' : 'تعذّر تحميل الملف.', response.status, 'DownloadError')
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = file.name
    document.body.appendChild(link)
    link.click()
    link.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 10_000)
  },

  /** The reader's thumbs on an answer; a thumbs-down answer is never repeated. */
  feedback: (answerId: string, feedback: 'up' | 'down' | null) =>
    api.post<{ recorded: boolean }>('/api/chat/feedback', { answer_id: answerId, feedback: feedback ?? '' }),
}
