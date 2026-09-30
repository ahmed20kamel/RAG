import { useEffect } from 'react'
import { useToasts, type Toast } from '@/state/toasts'
import { useTranslation } from '@/hooks/useTranslation'
import { IconButton } from './primitives'
import { cx } from '@/utils/cx'

function ToastRow({ toast }: { toast: Toast }) {
  const dismiss = useToasts((state) => state.dismiss)
  const { t } = useTranslation()

  useEffect(() => {
    if (!toast.duration) return
    const timer = window.setTimeout(() => dismiss(toast.id), toast.duration)
    return () => window.clearTimeout(timer)
  }, [toast.id, toast.duration, dismiss])

  return (
    <div className={cx('toast', `toast--${toast.tone}`)} role="status" aria-live="polite">
      <span className="toast__accent" />
      <div className="toast__content">
        <div className="toast__title">{toast.title}</div>
        {toast.description && <div className="toast__description">{toast.description}</div>}
      </div>
      <IconButton icon="close" label={t('common.dismiss')} size="sm" onClick={() => dismiss(toast.id)} />
    </div>
  )
}

export function ToastViewport() {
  const toasts = useToasts((state) => state.toasts)
  if (toasts.length === 0) return null
  return (
    <div className="toast-viewport">
      {toasts.map((toast) => (
        <ToastRow key={toast.id} toast={toast} />
      ))}
    </div>
  )
}
