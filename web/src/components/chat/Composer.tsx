import { forwardRef, useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { Icon } from '@/components/ui/Icon'
import { Button, IconButton, Kbd, Spinner } from '@/components/ui/primitives'
import { useRecorder } from '@/hooks/useRecorder'
import { useTranslation } from '@/hooks/useTranslation'
import { chatApi } from '@/services/chat'
import { ApiError } from '@/services/client'
import { toast } from '@/state/toasts'
import type { ChatPicture } from '@/types/api'
import { PictureMarker } from './PictureMarker'

const MAX_ROWS_HEIGHT = 200

export interface ComposerProps {
  onSubmit: (question: string, picture?: ChatPicture) => void
  onStop: () => void
  pending: boolean
  disabled?: boolean
}

export const Composer = forwardRef<HTMLTextAreaElement, ComposerProps>(function Composer(
  { onSubmit, onStop, pending, disabled },
  ref,
) {
  const { t } = useTranslation()
  const [value, setValue] = useState('')
  const [menuOpen, setMenuOpen] = useState(false)
  const [marking, setMarking] = useState<File | null>(null)
  const [picture, setPicture] = useState<ChatPicture | null>(null)
  const [transcribing, setTranscribing] = useState(false)
  const inner = useRef<HTMLTextAreaElement | null>(null)
  const filePicker = useRef<HTMLInputElement>(null)
  const menu = useRef<HTMLDivElement>(null)

  const recorder = useRecorder(async (clip) => {
    setTranscribing(true)
    try {
      const { text } = await chatApi.transcribe(clip)
      if (!text.trim()) toast.info(t('voice.empty'))
      // Into the box, not sent: speech recognition makes mistakes the reader should see.
      else setValue((current) => (current.trim() ? `${current.trim()} ${text}` : text))
      inner.current?.focus()
    } catch (error) {
      toast.error(t('voice.failed'), error instanceof ApiError ? error.message : undefined)
    } finally {
      setTranscribing(false)
    }
  })

  // Grows with the text up to a ceiling, then scrolls — a composer that keeps growing
  // eventually pushes the conversation off the screen.
  useEffect(() => {
    const node = inner.current
    if (!node) return
    node.style.height = 'auto'
    node.style.height = `${Math.min(node.scrollHeight, MAX_ROWS_HEIGHT)}px`
  }, [value])

  useEffect(() => {
    if (!menuOpen) return
    const close = (event: MouseEvent | globalThis.KeyboardEvent) => {
      if (event instanceof KeyboardEvent ? event.key === 'Escape' : !menu.current?.contains(event.target as Node))
        setMenuOpen(false)
    }
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', close)
    return () => {
      document.removeEventListener('mousedown', close)
      document.removeEventListener('keydown', close)
    }
  }, [menuOpen])

  const submit = () => {
    const question = value.trim() || (picture ? t('picture.defaultQuestion') : '')
    if (!question || pending || disabled || recorder.state === 'recording' || transcribing) return
    setValue('')
    setPicture(null)
    onSubmit(question, picture ?? undefined)
  }

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      submit()
    }
  }

  const voice = () => {
    setMenuOpen(false)
    if (recorder.state === 'unsupported') {
      toast.error(t('voice.title'), t('voice.unsupported'))
      return
    }
    void recorder.start()
  }

  useEffect(() => {
    if (recorder.error === 'denied') toast.error(t('voice.title'), t('voice.denied'))
  }, [recorder.error, t])

  const recording = recorder.state === 'recording'

  return (
    <div className="composer">
      {picture && (
        <div className="composer__attachment">
          <img src={picture.thumbnail} alt="" className="composer__thumb" />
          <div className="composer__attachment-text">
            <strong>{t('picture.attached')}</strong>
            <span className={picture.hasText ? undefined : 'composer__attachment-warn'}>
              {picture.marked
                ? t('picture.markedRead', { text: picture.marked.slice(0, 90) })
                : picture.hasText
                  ? t('picture.textRead')
                  : t('picture.noText')}
            </span>
          </div>
          <IconButton icon="close" label={t('picture.remove')} size="sm" onClick={() => setPicture(null)} />
        </div>
      )}
      <div className="composer__box">
        <div className="composer__more" ref={menu}>
          <IconButton
            icon="plus"
            label={t('composer.more')}
            onClick={() => setMenuOpen((open) => !open)}
            disabled={disabled || recording || transcribing}
            aria-expanded={menuOpen}
          />
          {menuOpen && (
            <div className="composer__menu" role="menu">
              <button type="button" role="menuitem" onClick={() => { setMenuOpen(false); filePicker.current?.click() }}>
                <Icon name="image" size={16} />
                <span>
                  {t('picture.menu')}
                  <small>{t('picture.menuHint')}</small>
                </span>
              </button>
              <button type="button" role="menuitem" onClick={voice}>
                <Icon name="mic" size={16} />
                <span>
                  {t('voice.menu')}
                  <small>{t('voice.menuHint')}</small>
                </span>
              </button>
            </div>
          )}
          <input
            ref={filePicker}
            type="file"
            accept="image/*"
            hidden
            onChange={(event) => {
              const chosen = event.target.files?.[0]
              event.target.value = ''
              if (chosen) setMarking(chosen)
            }}
          />
        </div>
        {recording ? (
          <div className="composer__recording" role="status">
            <span className="composer__rec-dot" aria-hidden="true" />
            {t('voice.recording', { seconds: recorder.seconds })}
          </div>
        ) : (
          <textarea
            ref={(node) => {
              inner.current = node
              if (typeof ref === 'function') ref(node)
              else if (ref) ref.current = node
            }}
            className="composer__input"
            rows={1}
            value={value}
            disabled={disabled || transcribing}
            placeholder={transcribing ? t('voice.transcribing') : t('chat.placeholder')}
            onChange={(event) => setValue(event.target.value)}
            onKeyDown={onKeyDown}
            aria-label={t('chat.placeholder')}
          />
        )}
        {transcribing && <Spinner size={15} />}
        {recording ? (
          <Button variant="danger" size="sm" icon="stop" onClick={recorder.stop}>
            {t('voice.stop')}
          </Button>
        ) : pending ? (
          <Button variant="secondary" size="sm" icon="stop" onClick={onStop}>
            {t('chat.stop')}
          </Button>
        ) : (
          <IconButton
            icon="send"
            label={t('chat.send')}
            onClick={submit}
            disabled={(!value.trim() && !picture) || disabled || transcribing}
          />
        )}
      </div>
      <p className="composer__hint">
        <Kbd>Enter</Kbd> {t('shortcuts.send')} · <Kbd>Shift</Kbd>
        <Kbd>Enter</Kbd> {t('shortcuts.newline')}
      </p>
      <PictureMarker
        file={marking}
        onClose={() => setMarking(null)}
        onAttach={(read) => {
          setMarking(null)
          setPicture(read)
          inner.current?.focus()
        }}
      />
    </div>
  )
})
