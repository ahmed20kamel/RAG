import { forwardRef, useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { Button, IconButton, Kbd } from '@/components/ui/primitives'
import { useTranslation } from '@/hooks/useTranslation'

const MAX_ROWS_HEIGHT = 200

export interface ComposerProps {
  onSubmit: (question: string) => void
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
  const inner = useRef<HTMLTextAreaElement | null>(null)

  // Grows with the text up to a ceiling, then scrolls — a composer that keeps growing
  // eventually pushes the conversation off the screen.
  useEffect(() => {
    const node = inner.current
    if (!node) return
    node.style.height = 'auto'
    node.style.height = `${Math.min(node.scrollHeight, MAX_ROWS_HEIGHT)}px`
  }, [value])

  const submit = () => {
    const question = value.trim()
    if (!question || pending || disabled) return
    setValue('')
    onSubmit(question)
  }

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      submit()
    }
  }

  return (
    <div className="composer">
      <div className="composer__box">
        <textarea
          ref={(node) => {
            inner.current = node
            if (typeof ref === 'function') ref(node)
            else if (ref) ref.current = node
          }}
          className="composer__input"
          rows={1}
          value={value}
          disabled={disabled}
          placeholder={t('chat.placeholder')}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={onKeyDown}
          aria-label={t('chat.placeholder')}
        />
        {pending ? (
          <Button variant="secondary" size="sm" icon="stop" onClick={onStop}>
            {t('chat.stop')}
          </Button>
        ) : (
          <IconButton
            icon="send"
            label={t('chat.send')}
            onClick={submit}
            disabled={!value.trim() || disabled}
          />
        )}
      </div>
      <p className="composer__hint">
        <Kbd>Enter</Kbd> {t('shortcuts.send')} · <Kbd>Shift</Kbd>
        <Kbd>Enter</Kbd> {t('shortcuts.newline')}
      </p>
    </div>
  )
})
