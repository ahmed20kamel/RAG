import { useCallback, useEffect, useRef, useState } from 'react'

/** Long enough for a question, short enough that a forgotten recording stops itself. */
const MAX_SECONDS = 120

export type RecorderState = 'idle' | 'recording' | 'unsupported'

/**
 * The microphone, recorded in the browser and handed back as one clip.
 *
 * Only the recording happens here; turning it into text is the server's job, on this
 * company's machine. A browser's built-in dictation would send the audio elsewhere.
 * Recording needs a secure page (https or this machine): elsewhere the browser refuses
 * the microphone, and the state says so instead of failing silently.
 */
export function useRecorder(onClip: (clip: Blob) => void) {
  const [state, setState] = useState<RecorderState>(() =>
    typeof window !== 'undefined' && window.isSecureContext && navigator.mediaDevices && 'MediaRecorder' in window
      ? 'idle'
      : 'unsupported',
  )
  const [seconds, setSeconds] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const recorder = useRef<MediaRecorder | null>(null)
  const timer = useRef<number | null>(null)
  const deliver = useRef(onClip)
  deliver.current = onClip

  const clear = () => {
    if (timer.current !== null) window.clearInterval(timer.current)
    timer.current = null
  }

  const stop = useCallback(() => {
    clear()
    if (recorder.current && recorder.current.state !== 'inactive') recorder.current.stop()
  }, [])

  const start = useCallback(async () => {
    setError(null)
    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch {
      setError('denied')
      return
    }
    const chunks: Blob[] = []
    const media = new MediaRecorder(stream)
    media.ondataavailable = (event) => {
      if (event.data.size) chunks.push(event.data)
    }
    media.onstop = () => {
      stream.getTracks().forEach((track) => track.stop())
      recorder.current = null
      setState('idle')
      if (chunks.length) deliver.current(new Blob(chunks, { type: media.mimeType || 'audio/webm' }))
    }
    recorder.current = media
    media.start()
    setSeconds(0)
    setState('recording')
    timer.current = window.setInterval(() => {
      setSeconds((current) => {
        if (current + 1 >= MAX_SECONDS) stop()
        return current + 1
      })
    }, 1000)
  }, [stop])

  // Leaving the page mid-recording releases the microphone.
  useEffect(() => () => {
    clear()
    recorder.current?.stream.getTracks().forEach((track) => track.stop())
  }, [])

  return { state, seconds, error, start, stop }
}
