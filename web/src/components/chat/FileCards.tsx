import { useState } from 'react'
import { Icon } from '@/components/ui/Icon'
import { Button } from '@/components/ui/primitives'
import { useTranslation } from '@/hooks/useTranslation'
import { chatApi } from '@/services/chat'
import { ApiError } from '@/services/client'
import { toast } from '@/state/toasts'
import type { ChatFile } from '@/types/api'
import { formatBytes } from '@/utils/format'

const LABEL: Record<string, string> = { pdf: 'PDF', docx: 'Word', xlsx: 'Excel' }

/** The files a reply made, each with its download. */
export function FileCards({ files }: { files: ChatFile[] }) {
  const { t, language } = useTranslation()
  const [busy, setBusy] = useState<string | null>(null)

  const download = async (file: ChatFile) => {
    setBusy(file.id)
    try {
      await chatApi.downloadFile(file)
    } catch (error) {
      toast.error(t('files.failed'), error instanceof ApiError ? error.message : undefined)
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="file-cards">
      {files.map((file) => (
        <div key={file.id} className={`file-card file-card--${file.format}`}>
          <span className="file-card__icon" aria-hidden="true">
            <Icon name="file" size={20} />
            <small>{LABEL[file.format] ?? file.format.toUpperCase()}</small>
          </span>
          <span className="file-card__text">
            <strong title={file.name}>{file.name}</strong>
            <small>
              {LABEL[file.format] ?? file.format} · {formatBytes(file.size, language)} · {t('files.keep')}
            </small>
          </span>
          <Button size="sm" icon="download" className="file-card__download" disabled={busy === file.id}
            onClick={() => void download(file)}>
            {busy === file.id ? t('files.downloading') : t('files.download')}
          </Button>
        </div>
      ))}
    </div>
  )
}
