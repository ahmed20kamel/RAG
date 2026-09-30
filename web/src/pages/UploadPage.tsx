import { useCallback, useEffect, useRef, useState, type DragEvent } from 'react'
import { Link } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { Icon } from '@/components/ui/Icon'
import { Badge, Button, Card, CardHeader, Field, IconButton, Input, Progress } from '@/components/ui/primitives'
import { cx } from '@/utils/cx'
import { PipelineSteps, StatusPill } from '@/components/documents/StatusPill'
import '@/components/documents/documents.css'
import { documentKeys, isInFlight, useDocument } from '@/hooks/useDocuments'
import { useConfig } from '@/hooks/useSystem'
import { useTranslation, type TranslationKey } from '@/hooks/useTranslation'
import { documentsApi } from '@/services/documents'
import { toast } from '@/state/toasts'
import { fileExtension, formatBytes } from '@/utils/format'
import type { UploadMetadata } from '@/types/api'

const MAX_SIZE = 20 * 1024 * 1024
/** Used only until `/api/system/config` answers; the backend stays the authority. */
const FALLBACK_EXTENSIONS = ['.md', '.markdown']

const META_FIELDS: Array<{ key: keyof UploadMetadata; label: TranslationKey; type?: string }> = [
  { key: 'title', label: 'upload.fields.title' },
  { key: 'category', label: 'upload.fields.category' },
  { key: 'source', label: 'upload.fields.source' },
  { key: 'version', label: 'upload.fields.version' },
  { key: 'date', label: 'upload.fields.date', type: 'date' },
  { key: 'language', label: 'upload.fields.language' },
]

type Rejection = 'type' | 'size' | 'empty'

/**
 * `processing` means the file reached the server: from there the row shows the document's
 * real status and stops tracking anything locally.
 */
type ItemState = 'waiting' | 'uploading' | 'processing' | 'settled' | 'error'

interface QueueItem {
  key: string
  file: File
  state: ItemState
  documentId?: string
  error?: string
}

function QueueRow({
  item,
  onSettled,
  onRemove,
}: {
  item: QueueItem
  onSettled: (key: string) => void
  onRemove: (key: string) => void
}) {
  const { t, language } = useTranslation()
  const { data } = useDocument(item.documentId)
  const announced = useRef(false)
  const status = data?.status
  const errorMessage = data?.error_message

  useEffect(() => {
    if (!status || isInFlight(status) || announced.current) return
    announced.current = true
    onSettled(item.key)
    if (status === 'completed') toast.success(t('upload.ready'), item.file.name)
    else toast.error(t('upload.failed'), errorMessage || item.file.name)
  }, [status, errorMessage, item.key, item.file.name, onSettled, t])

  const localLabel =
    item.state === 'error' ? t('upload.failed') : item.state === 'uploading' ? t('upload.uploading') : t('upload.waiting')

  return (
    <li className="upload-item">
      <div className="upload-item__head">
        <Icon name="file" size={16} className="upload-item__icon" />
        <span className="upload-item__name">{item.file.name}</span>
        <span className="upload-item__meta">{formatBytes(item.file.size, language)}</span>
        {status ? (
          <StatusPill status={status} />
        ) : (
          <Badge tone={item.state === 'error' ? 'danger' : 'info'} dot={item.state === 'uploading'}>
            {localLabel}
          </Badge>
        )}
        <IconButton icon="close" label={t('common.dismiss')} size="sm" onClick={() => onRemove(item.key)} />
      </div>

      {item.state === 'uploading' && <Progress label={t('upload.uploading')} />}
      {item.error && <p className="upload-item__error">{item.error}</p>}
      {data && <PipelineSteps status={data.status} errorMessage={data.error_message} />}
      {data?.status === 'completed' && (
        <Link className="upload-item__link" to={`/documents/${data.id}`}>
          {t('upload.openDocument')}
          <Icon name="chevronEnd" size={13} />
        </Link>
      )}
    </li>
  )
}

export function UploadPage() {
  const { t, language } = useTranslation()
  const client = useQueryClient()
  const config = useConfig()
  const input = useRef<HTMLInputElement>(null)

  const [items, setItems] = useState<QueueItem[]>([])
  const [dragging, setDragging] = useState(false)
  const [metaOpen, setMetaOpen] = useState(false)
  const [metadata, setMetadata] = useState<UploadMetadata>({})
  const [busy, setBusy] = useState(false)

  const extensions = config.data?.supported_extensions ?? FALLBACK_EXTENSIONS

  const rejectionOf = useCallback(
    (file: File): Rejection | null => {
      if (!extensions.includes(fileExtension(file.name))) return 'type'
      if (file.size === 0) return 'empty'
      if (file.size > MAX_SIZE) return 'size'
      return null
    },
    [extensions],
  )

  const addFiles = useCallback(
    (files: FileList | null) => {
      if (!files || files.length === 0) return
      const added: QueueItem[] = []
      for (const file of Array.from(files)) {
        const key = `${file.name}-${file.size}-${file.lastModified}-${Math.random().toString(36).slice(2, 8)}`
        const rejection = rejectionOf(file)
        if (rejection) {
          const reason = t(`upload.rejected.${rejection}`)
          added.push({ key, file, state: 'error', error: reason })
          toast.error(reason, file.name)
        } else {
          added.push({ key, file, state: 'waiting' })
        }
      }
      setItems((previous) => [...previous, ...added])
    },
    [rejectionOf, t],
  )

  const markSettled = useCallback((key: string) => {
    setItems((previous) => previous.map((item) => (item.key === key ? { ...item, state: 'settled' } : item)))
  }, [])

  const removeItem = useCallback((key: string) => {
    setItems((previous) => previous.filter((item) => item.key !== key))
  }, [])

  async function uploadAll() {
    const pending = items.filter((item) => item.state === 'waiting')
    if (pending.length === 0) return
    setBusy(true)
    // Sequential: a queue of large files uploaded in parallel only makes every one of them
    // slower, and the backend indexes one document at a time anyway.
    for (const pendingItem of pending) {
      setItems((previous) =>
        previous.map((item) => (item.key === pendingItem.key ? { ...item, state: 'uploading' } : item)),
      )
      try {
        const document = await documentsApi.upload(pendingItem.file, metadata)
        setItems((previous) =>
          previous.map((item) =>
            item.key === pendingItem.key
              ? { ...item, state: 'processing', documentId: document.id, error: undefined }
              : item,
          ),
        )
      } catch (error) {
        const message = error instanceof Error ? error.message : t('errors.generic')
        setItems((previous) =>
          previous.map((item) => (item.key === pendingItem.key ? { ...item, state: 'error', error: message } : item)),
        )
        toast.error(t('upload.failed'), `${pendingItem.file.name} — ${message}`)
      }
    }
    setBusy(false)
    void client.invalidateQueries({ queryKey: documentKeys.all })
  }

  function onDrop(event: DragEvent<HTMLButtonElement>) {
    event.preventDefault()
    setDragging(false)
    addFiles(event.dataTransfer.files)
  }

  const waiting = items.filter((item) => item.state === 'waiting').length
  const finished = items.filter((item) => item.state === 'settled' || item.state === 'error').length

  return (
    <div className="page">
      <header className="page__head">
        <div>
          <h1 className="page__title">{t('upload.title')}</h1>
          <p className="page__subtitle">{t('upload.subtitle', { types: extensions.join(' · ') })}</p>
        </div>
      </header>

      <button
        type="button"
        className={cx('dropzone', dragging && 'dropzone--active')}
        onClick={() => input.current?.click()}
        onDragEnter={(event) => {
          event.preventDefault()
          setDragging(true)
        }}
        onDragOver={(event) => {
          event.preventDefault()
          setDragging(true)
        }}
        onDragLeave={(event) => {
          // Moving over a child fires dragleave on the zone; only a real exit clears it.
          if (event.currentTarget.contains(event.relatedTarget as Node | null)) return
          setDragging(false)
        }}
        onDrop={onDrop}
      >
        <span className="dropzone__icon">
          <Icon name="upload" size={20} />
        </span>
        <span className="dropzone__title">{dragging ? t('upload.dropzoneActive') : t('upload.dropzone')}</span>
        <span className="dropzone__hint">{extensions.join(' · ')}</span>
        <span className="dropzone__hint">{t('upload.maxSize', { size: formatBytes(MAX_SIZE, language) })}</span>
      </button>

      <input
        ref={input}
        type="file"
        multiple
        hidden
        accept={extensions.join(',')}
        aria-label={t('upload.browse')}
        onChange={(event) => {
          addFiles(event.target.files)
          // Reset, so choosing the same file twice in a row still fires a change.
          event.target.value = ''
        }}
      />

      <p className="upload-note">
        <Icon name="info" size={14} />
        <span>{t('upload.pipelineNote')}</span>
      </p>

      <Card>
        <CardHeader
          title={t('upload.metadata')}
          action={
            <Button variant="ghost" size="sm" iconEnd="chevronDown" onClick={() => setMetaOpen(!metaOpen)}>
              {metaOpen ? t('common.showLess') : t('common.showMore')}
            </Button>
          }
        />
        {metaOpen && (
          <div className="upload-meta">
            <p className="upload-meta__hint">{t('upload.metadataHint')}</p>
            {META_FIELDS.map(({ key, label, type }) => (
              <Field key={key} label={t(label)} htmlFor={`upload-${key}`}>
                <Input
                  id={`upload-${key}`}
                  type={type ?? 'text'}
                  value={metadata[key] ?? ''}
                  onChange={(event) => {
                    const value = event.target.value
                    setMetadata((previous): UploadMetadata => ({ ...previous, [key]: value }))
                  }}
                />
              </Field>
            ))}
          </div>
        )}
      </Card>

      {items.length > 0 && (
        <Card>
          <CardHeader
            title={t('upload.queue')}
            action={
              <div className="upload-actions">
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={finished === 0}
                  onClick={() => setItems((previous) => previous.filter((item) => item.state !== 'settled' && item.state !== 'error'))}
                >
                  {t('upload.clearFinished')}
                </Button>
                <Button
                  variant="primary"
                  size="sm"
                  icon="upload"
                  loading={busy}
                  disabled={waiting === 0}
                  onClick={() => void uploadAll()}
                >
                  {t('upload.uploadAll')}
                </Button>
              </div>
            }
          />
          <ul className="upload-queue">
            {items.map((item) => (
              <QueueRow key={item.key} item={item} onSettled={markSettled} onRemove={removeItem} />
            ))}
          </ul>
        </Card>
      )}
    </div>
  )
}
