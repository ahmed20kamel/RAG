import { useCallback, useRef, useState } from 'react'
import { chatApi } from '@/services/chat'
import { ApiError } from '@/services/client'
import type { ChatLoad, ChatRequest, ChatResponse, ChatStage } from '@/types/api'

export interface AskState {
  pending: boolean
  /** Stages the backend has reported for the request in flight, in arrival order. */
  stages: ChatStage[]
  /** How busy the server was for this request: questions ahead and the expected wait. */
  load: ChatLoad | null
  /** When the request in flight was sent. */
  startedAt: number | null
  ask: (request: ChatRequest) => Promise<ChatResponse | null>
  stop: () => void
}

export class StoppedError extends Error {
  constructor() {
    super('stopped')
    this.name = 'StoppedError'
  }
}

/**
 * Runs one question at a time and reports the pipeline's progress.
 *
 * The stages come from the backend as it reaches them — the hook never advances them
 * on a timer, so a stage shown is a stage that ran. Stopping aborts the request; the
 * server-side generation it triggered finishes on its own, but its result is discarded.
 */
export function useAsk(): AskState {
  const [pending, setPending] = useState(false)
  const [stages, setStages] = useState<ChatStage[]>([])
  const [load, setLoad] = useState<ChatLoad | null>(null)
  const [startedAt, setStartedAt] = useState<number | null>(null)
  const controller = useRef<AbortController | null>(null)

  const stop = useCallback(() => {
    controller.current?.abort()
    controller.current = null
    setPending(false)
    setStages([])
    setLoad(null)
    setStartedAt(null)
  }, [])

  const ask = useCallback(async (request: ChatRequest) => {
    controller.current?.abort()
    const abort = new AbortController()
    controller.current = abort
    setStages([])
    setLoad(null)
    setStartedAt(Date.now())
    setPending(true)

    let answer: ChatResponse | null = null
    let failure: string | null = null

    try {
      await chatApi.askStreaming(
        request,
        (event) => {
          if (event.type === 'stage') setStages((current) => [...current, event.stage])
          else if (event.type === 'result') answer = event.response
          else if (event.type === 'error') failure = event.detail
          else if (event.type === 'load')
            setLoad({ ahead: event.ahead, waitSeconds: event.wait_seconds, at: Date.now() })
          // 'ping' only keeps the connection open while the model writes.
        },
        abort.signal,
      )
    } catch (error) {
      if (abort.signal.aborted) throw new StoppedError()
      // A backend without the streaming route still answers the plain endpoint, so the
      // interface keeps working rather than failing on a missing capability.
      if (error instanceof ApiError && error.status === 404) {
        answer = await chatApi.ask(request, abort.signal)
      } else {
        throw error
      }
    } finally {
      if (controller.current === abort) controller.current = null
      setPending(false)
      setStages([])
      setLoad(null)
      setStartedAt(null)
    }

    if (failure) throw new ApiError(failure, 500, 'PipelineError')
    return answer
  }, [])

  return { pending, stages, load, startedAt, ask, stop }
}
