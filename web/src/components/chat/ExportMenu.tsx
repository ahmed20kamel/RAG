import { useEffect, useRef, useState } from 'react'
import { Icon } from '@/components/ui/Icon'
import { Button, Spinner } from '@/components/ui/primitives'
import { useTranslation } from '@/hooks/useTranslation'
import { chatApi } from '@/services/chat'
import { ApiError } from '@/services/client'
import { toast } from '@/state/toasts'
import type { ChatFile, ChatResponse } from '@/types/api'

const OPTIONS: Array<{ format: 'pdf' | 'docx' | 'xlsx'; label: string; hint: 'exportPdfHint' | 'exportWordHint' | 'exportExcelHint' }> = [
  { format: 'pdf', label: 'PDF', hint: 'exportPdfHint' },
  { format: 'docx', label: 'Word', hint: 'exportWordHint' },
  { format: 'xlsx', label: 'Excel', hint: 'exportExcelHint' },
]

/**
 * "Export" under an answer: the answer as a branded PDF, Word or Excel file in one click.
 * The file is made by the server from the answer on the screen and its cited sources, with
 * the same template and number checks as a file asked for in words.
 */
export function ExportMenu({
  title,
  markdown,
  response,
  onMade,
}: {
  title: string
  markdown: string
  response: ChatResponse
  onMade: (files: ChatFile[]) => void
}) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const box = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const close = (event: MouseEvent | KeyboardEvent) => {
      if (event instanceof KeyboardEvent ? event.key === 'Escape' : !box.current?.contains(event.target as Node))
        setOpen(false)
    }
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', close)
    return () => {
      document.removeEventListener('mousedown', close)
      document.removeEventListener('keydown', close)
    }
  }, [open])

  const make = async (format: 'pdf' | 'docx' | 'xlsx') => {
    setOpen(false)
    setBusy(format)
    try {
      const made = await chatApi.exportAnswer({ format, title, markdown, sources: response.sources })
      onMade(made.files)
      if (made.files[0]) await chatApi.downloadFile(made.files[0])
    } catch (error) {
      toast.error(t('chat.exportFailed'), error instanceof ApiError ? error.message : undefined)
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="export-menu" ref={box}>
      <Button variant="ghost" size="sm" icon="download" disabled={busy !== null} onClick={() => setOpen((v) => !v)}
        aria-expanded={open}>
        {busy ? <Spinner size={12} /> : null}
        {busy ? t('chat.exporting') : t('chat.export')}
      </Button>
      {open && (
        <div className="export-menu__list" role="menu">
          {OPTIONS.map((option) => (
            <button key={option.format} type="button" role="menuitem" onClick={() => void make(option.format)}>
              <span className={`export-menu__badge export-menu__badge--${option.format}`}>
                <Icon name="file" size={14} />
              </span>
              <span>
                <strong>{option.label}</strong>
                <small>{t(`chat.${option.hint}`)}</small>
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
