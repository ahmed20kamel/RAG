/**
 * The single place that talks to the backend.
 *
 * Every failure arrives as an `ApiError` carrying the status and the backend's own
 * Arabic message, so the interface can show what actually went wrong instead of a
 * generic "something failed".
 */

/** Empty in production: the client is served by the API, so requests are same-origin. */
export const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

export class ApiError extends Error {
  readonly status: number
  readonly kind: string

  constructor(message: string, status: number, kind = 'ApiError') {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.kind = kind
  }

  /** A request that failed before reaching the server — offline, DNS, refused. */
  get isNetwork(): boolean {
    return this.status === 0
  }
}

interface RequestOptions extends Omit<RequestInit, 'body'> {
  body?: unknown
  query?: Record<string, string | number | boolean | undefined | null>
}

function buildUrl(path: string, query?: RequestOptions['query']): string {
  const url = `${API_BASE}${path}`
  if (!query) return url
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && value !== '') params.set(key, String(value))
  }
  const search = params.toString()
  return search ? `${url}?${search}` : url
}

async function toApiError(response: Response): Promise<ApiError> {
  let detail = `${response.status} ${response.statusText}`
  let kind = 'HttpError'
  try {
    const payload = await response.json()
    if (typeof payload?.detail === 'string') detail = payload.detail
    else if (Array.isArray(payload?.detail)) {
      // FastAPI validation errors arrive as a list of field problems.
      detail = payload.detail.map((d: { msg?: string }) => d.msg ?? '').filter(Boolean).join(' — ')
    }
    if (typeof payload?.error === 'string') kind = payload.error
  } catch {
    // A non-JSON body (a proxy error page, say) leaves the status line as the message.
  }
  return new ApiError(detail, response.status, kind)
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { body, query, headers, ...rest } = options
  const isFormData = body instanceof FormData

  let response: Response
  try {
    response = await fetch(buildUrl(path, query), {
      credentials: 'include',
      ...rest,
      headers: {
        ...(isFormData || body === undefined ? {} : { 'Content-Type': 'application/json' }),
        ...headers,
      },
      body: isFormData ? body : body === undefined ? undefined : JSON.stringify(body),
    })
  } catch (error) {
    if ((error as Error).name === 'AbortError') throw error
    throw new ApiError('تعذّر الاتصال بالخادم. تأكد من أن الخدمة تعمل.', 0, 'NetworkError')
  }

  if (!response.ok) throw await toApiError(response)
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export const api = {
  get: <T>(path: string, options?: RequestOptions) => request<T>(path, { ...options, method: 'GET' }),
  post: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    request<T>(path, { ...options, method: 'POST', body }),
  patch: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    request<T>(path, { ...options, method: 'PATCH', body }),
  delete: <T>(path: string, options?: RequestOptions) =>
    request<T>(path, { ...options, method: 'DELETE' }),
  request,
}

/**
 * Reads a newline-delimited JSON stream, handing each complete line to `onEvent`.
 * Used by the chat stage stream, where events arrive as the pipeline reaches them.
 */
export async function streamNdjson<T>(
  path: string,
  body: unknown,
  onEvent: (event: T) => void,
  signal?: AbortSignal,
): Promise<void> {
  let response: Response
  try {
    response = await fetch(buildUrl(path), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      credentials: 'include',
      signal,
    })
  } catch (error) {
    if ((error as Error).name === 'AbortError') throw error
    throw new ApiError('تعذّر الاتصال بالخادم. تأكد من أن الخدمة تعمل.', 0, 'NetworkError')
  }

  if (!response.ok) throw await toApiError(response)
  if (!response.body) throw new ApiError('لم يرسل الخادم أي بيانات.', 502, 'EmptyStream')

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    let newline = buffer.indexOf('\n')
    while (newline !== -1) {
      const line = buffer.slice(0, newline).trim()
      buffer = buffer.slice(newline + 1)
      if (line) onEvent(JSON.parse(line) as T)
      newline = buffer.indexOf('\n')
    }
  }

  const tail = buffer.trim()
  if (tail) onEvent(JSON.parse(tail) as T)
}
