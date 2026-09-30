import { useEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import { PipelineSteps, StatusPill } from '@/components/documents/StatusPill'
import { ConfirmDialog } from '@/components/ui/Modal'
import { Badge, Button, Card, CardHeader, EmptyState, ErrorState, Field, IconButton, Select, Skeleton, Tabs, type TabItem } from '@/components/ui/primitives'
import { cx } from '@/utils/cx'
import { useCopy } from '@/hooks/useCopy'
import {
  isInFlight,
  useDeleteDocument,
  useDocument,
  useDocumentChunks,
  useDocumentEntities,
  useDocumentRaw,
  useDocumentSections,
  useReindexDocument,
} from '@/hooks/useDocuments'
import { useTranslation } from '@/hooks/useTranslation'
import { ApiError } from '@/services/client'
import { toast } from '@/state/toasts'
import type {
  DocumentChunkPage,
  DocumentRaw,
  DocumentResponse,
  EntityResponse,
  SectionResponse,
} from '@/types/api'
import { breadcrumbParts, formatBytes, formatDate, formatDateTime, formatNumber } from '@/utils/format'
import './document.css'

type DocumentTab = 'overview' | 'sections' | 'chunks' | 'entities' | 'source'

const SECTION_PREFIX = 'section-'
const CHUNK_PREFIX = 'chunk-'
const CHUNK_PAGE_SIZE = 20
/** A deep-linked chunk is found by walking pages; this caps the walk for huge documents. */
const MAX_SEEK_PAGES = 10
const HIGHLIGHT_MS = 2200
const DASH = '—'

/** `#section-<id>` / `#chunk-<id>` — how a chat citation points at its own source. */
function targetFromHash(hash: string): { tab: DocumentTab; elementId: string } | null {
  const elementId = decodeURIComponent(hash.replace(/^#/, ''))
  if (elementId.startsWith(SECTION_PREFIX)) return { tab: 'sections', elementId }
  if (elementId.startsWith(CHUNK_PREFIX)) return { tab: 'chunks', elementId }
  return null
}

function errorBody(error: unknown): string | undefined {
  return error instanceof Error ? error.message : undefined
}

function CopyButton({ text }: { text: string }) {
  const { t } = useTranslation()
  const { copied, copy } = useCopy()
  return (
    <IconButton
      size="sm"
      icon={copied ? 'check' : 'copy'}
      label={copied ? t('common.copied') : t('common.copy')}
      onClick={() => void copy(text)}
    />
  )
}

function PanelSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="doc-panel__skeleton">
      {Array.from({ length: rows }, (_, index) => (
        <Skeleton key={index} height="5rem" radius="14px" />
      ))}
    </div>
  )
}

function PanelError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const { t } = useTranslation()
  return (
    <ErrorState
      title={t('errors.title')}
      body={errorBody(error)}
      action={
        <Button size="sm" icon="refresh" onClick={onRetry}>
          {t('common.retry')}
        </Button>
      }
    />
  )
}

/* Overview ---------------------------------------------------------------- */

function OverviewPanel({ doc }: { doc: DocumentResponse }) {
  const { t, language } = useTranslation()

  const rows: Array<[string, string]> = [
    [t('document.category'), doc.category || DASH],
    [t('document.version'), doc.version || DASH],
    [t('document.docDate'), doc.doc_date ? formatDate(doc.doc_date, language) : DASH],
    [t('document.language'), doc.language || DASH],
    [t('document.charCount'), formatNumber(doc.char_count, language)],
    [t('document.size'), formatBytes(doc.size_bytes, language)],
    [t('library.chunks'), formatNumber(doc.chunk_count, language)],
    [t('document.uploadedAt'), formatDateTime(doc.uploaded_at, language)],
    [t('document.indexedAt'), formatDateTime(doc.indexed_at, language)],
    [t('document.updatedAt'), formatDateTime(doc.updated_at, language)],
  ]

  return (
    <div className="doc-overview">
      {doc.status === 'failed' && (
        <Card className="doc-failure" padded>
          <strong className="doc-failure__title">{t('document.failedNote')}</strong>
          {doc.error_message && <p className="doc-failure__message">{doc.error_message}</p>}
        </Card>
      )}

      <Card>
        <CardHeader title={t('document.metadata')} />
        <dl className="doc-meta">
          {rows.map(([label, value]) => (
            <div key={label} className="doc-meta__row">
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
      </Card>

      <Card>
        <CardHeader title={t('document.processing')} />
        <div className="card__body">
          <PipelineSteps status={doc.status} />
        </div>
      </Card>
    </div>
  )
}

/* Sections ---------------------------------------------------------------- */

function SectionsPanel({
  sections,
  isLoading,
  error,
  onRetry,
  highlighted,
}: {
  sections: SectionResponse[] | undefined
  isLoading: boolean
  error: unknown
  onRetry: () => void
  highlighted: string | null
}) {
  const { t, language } = useTranslation()

  if (isLoading) return <PanelSkeleton />
  if (error) return <PanelError error={error} onRetry={onRetry} />
  if (!sections?.length) return <EmptyState icon="layers" title={t('document.noSections')} />

  return (
    <ol className="doc-sections">
      {sections.map((section) => {
        const elementId = `${SECTION_PREFIX}${section.section_id}`
        const depth = Math.min(Math.max(section.level - 1, 0), 5)
        const crumbs = breadcrumbParts(section.path)
        return (
          <li
            key={section.section_id}
            id={elementId}
            className={cx('doc-section', highlighted === elementId && 'is-deeplinked')}
            style={{ paddingInlineStart: `calc(var(--space-4) + ${depth} * var(--space-5))` }}
          >
            <div className="doc-section__head">
              <span className="doc-section__level">H{section.level}</span>
              <h3 className="doc-section__heading">{section.heading || DASH}</h3>
              <span className="doc-section__chars">{formatNumber(section.char_count, language)}</span>
            </div>

            {crumbs.length > 0 && (
              <p className="doc-section__path">
                {crumbs.map((crumb, index) => (
                  <span key={`${crumb}-${index}`} className="doc-section__crumb">
                    {crumb}
                  </span>
                ))}
              </p>
            )}

            {section.summary && (
              <p className="doc-section__summary">
                <span className="doc-section__label">{t('document.summary')}</span>
                {section.summary}
              </p>
            )}

            {section.terms.length > 0 && (
              <div className="doc-section__terms" aria-label={t('document.terms')}>
                {section.terms.map((term) => (
                  <Badge key={term} tone="neutral">
                    {term}
                  </Badge>
                ))}
              </div>
            )}

            {(section.has_table || section.has_list || section.has_code) && (
              <div className="doc-section__flags">
                {section.has_table && <Badge tone="info">{t('document.hasTable')}</Badge>}
                {section.has_list && <Badge tone="info">{t('document.hasList')}</Badge>}
                {section.has_code && <Badge tone="info">{t('document.hasCode')}</Badge>}
              </div>
            )}
          </li>
        )
      })}
    </ol>
  )
}

/* Chunks ------------------------------------------------------------------ */

function ChunksPanel({
  page,
  isLoading,
  error,
  onRetry,
  offset,
  onOffset,
  highlighted,
}: {
  page: DocumentChunkPage | undefined
  isLoading: boolean
  error: unknown
  onRetry: () => void
  offset: number
  onOffset: (offset: number) => void
  highlighted: string | null
}) {
  const { t, language } = useTranslation()

  if (isLoading && !page) return <PanelSkeleton />
  if (error) return <PanelError error={error} onRetry={onRetry} />
  if (!page?.items.length) return <EmptyState icon="layers" title={t('document.noChunks')} />

  const first = page.offset + 1
  const last = page.offset + page.items.length

  return (
    <div className="doc-chunks">
      {page.items.map((chunk) => {
        const elementId = `${CHUNK_PREFIX}${chunk.chunk_id}`
        return (
          <article
            key={chunk.chunk_id}
            id={elementId}
            className={cx('doc-chunk', highlighted === elementId && 'is-deeplinked')}
          >
            <header className="doc-chunk__head">
              <span className="doc-chunk__index">
                {t('document.chunkOf', { index: formatNumber(chunk.index, language) })}
              </span>
              <span className="doc-chunk__heading">{chunk.heading || chunk.section || DASH}</span>
              <span className="doc-chunk__chars">{formatNumber(chunk.char_count, language)}</span>
              <CopyButton text={chunk.content} />
            </header>
            <p className="doc-chunk__content">{chunk.content}</p>
          </article>
        )
      })}

      <div className="doc-pager">
        <span className="doc-pager__count">
          {t('library.rowsShown', { shown: `${first}–${last}`, total: page.total })}
        </span>
        <div className="doc-pager__buttons">
          <Button
            size="sm"
            disabled={offset === 0}
            onClick={() => onOffset(Math.max(0, offset - CHUNK_PAGE_SIZE))}
          >
            {t('library.previous')}
          </Button>
          <Button
            size="sm"
            disabled={last >= page.total}
            onClick={() => onOffset(offset + CHUNK_PAGE_SIZE)}
          >
            {t('library.next')}
          </Button>
        </div>
      </div>
    </div>
  )
}

/* Entities ---------------------------------------------------------------- */

function EntitiesPanel({
  entities,
  isLoading,
  error,
  onRetry,
  kind,
  kinds,
  onKind,
}: {
  entities: EntityResponse[] | undefined
  isLoading: boolean
  error: unknown
  onRetry: () => void
  kind: string
  kinds: string[]
  onKind: (kind: string) => void
}) {
  const { t } = useTranslation()

  if (isLoading) return <PanelSkeleton />
  if (error) return <PanelError error={error} onRetry={onRetry} />
  if (!entities?.length) return <EmptyState icon="tag" title={t('document.noEntities')} />

  const visible = kind ? entities.filter((entity) => entity.kind === kind) : entities

  return (
    <div className="doc-entities">
      <div className="doc-entities__filter">
        <Field label={t('document.entityKind')} htmlFor="entity-kind">
          <Select id="entity-kind" value={kind} onChange={(event) => onKind(event.target.value)}>
            <option value="">{t('document.allKinds')}</option>
            {kinds.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </Select>
        </Field>
      </div>

      {visible.length === 0 ? (
        <EmptyState icon="tag" title={t('document.noEntities')} />
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>{t('document.entityKind')}</th>
                <th>{t('document.entityLabel')}</th>
                <th>{t('document.entityValue')}</th>
                <th>{t('document.entityContext')}</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((entity, index) => (
                <tr key={`${entity.kind}-${entity.value}-${index}`}>
                  <td>
                    <Badge tone="accent">{entity.kind}</Badge>
                  </td>
                  <td>{entity.label || DASH}</td>
                  <td className="doc-entities__value">{entity.value || DASH}</td>
                  <td className="doc-entities__context">{entity.context || DASH}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

/* Source ------------------------------------------------------------------ */

function SourcePanel({
  raw,
  isLoading,
  error,
  onRetry,
}: {
  raw: DocumentRaw | undefined
  isLoading: boolean
  error: unknown
  onRetry: () => void
}) {
  const { t, language } = useTranslation()

  if (isLoading) return <PanelSkeleton rows={1} />
  if (error) return <PanelError error={error} onRetry={onRetry} />
  if (!raw) return <EmptyState icon="file" title={t('document.noChunks')} />

  return (
    <Card className="doc-raw">
      <CardHeader
        title={
          <span className="doc-raw__title">
            {t('document.rawTitle')}
            <span className="doc-raw__chars">{formatNumber(raw.char_count, language)}</span>
          </span>
        }
        action={<CopyButton text={raw.content} />}
      />
      <pre className="doc-raw__body" dir="auto">
        {raw.content}
      </pre>
    </Card>
  )
}

/* Page -------------------------------------------------------------------- */

export function DocumentPage() {
  const { documentId } = useParams<{ documentId: string }>()
  const location = useLocation()
  const navigate = useNavigate()
  const { t, language } = useTranslation()

  const initialTarget = useRef(targetFromHash(location.hash))
  const [tab, setTab] = useState<DocumentTab>(initialTarget.current?.tab ?? 'overview')
  const [pendingTarget, setPendingTarget] = useState<string | null>(
    initialTarget.current?.elementId ?? null,
  )
  const [highlighted, setHighlighted] = useState<string | null>(null)
  const [chunkOffset, setChunkOffset] = useState(0)
  const [kind, setKind] = useState('')
  const [confirmOpen, setConfirmOpen] = useState(false)
  const highlightTimer = useRef<number>()

  const detail = useDocument(documentId)
  const sectionsQuery = useDocumentSections(documentId)
  const entitiesQuery = useDocumentEntities(documentId)
  const seekingChunk = pendingTarget?.startsWith(CHUNK_PREFIX) ?? false
  const chunksQuery = useDocumentChunks(
    documentId,
    chunkOffset,
    CHUNK_PAGE_SIZE,
    tab === 'chunks' || seekingChunk,
  )
  const rawQuery = useDocumentRaw(documentId, tab === 'source')

  const reindex = useReindexDocument()
  const remove = useDeleteDocument()

  const doc = detail.data
  const sections = sectionsQuery.data
  const entities = entitiesQuery.data
  const chunkPage = chunksQuery.data

  const kinds = useMemo(
    () => Array.from(new Set((entities ?? []).map((entity) => entity.kind))).sort(),
    [entities],
  )

  useEffect(() => () => window.clearTimeout(highlightTimer.current), [])

  useEffect(() => {
    setChunkOffset(0)
  }, [documentId])

  // A citation can be followed while the page is already open, so the hash is watched
  // rather than read once at mount.
  useEffect(() => {
    const target = targetFromHash(location.hash)
    if (!target) return
    setTab(target.tab)
    setPendingTarget(target.elementId)
  }, [location.hash])

  // The link names a chunk id, not the page it sits on; step forward until it appears
  // instead of loading every chunk of the document at once.
  useEffect(() => {
    if (!pendingTarget?.startsWith(CHUNK_PREFIX)) return
    if (!chunkPage || chunkPage.offset !== chunkOffset) return
    const chunkId = pendingTarget.slice(CHUNK_PREFIX.length)
    if (chunkPage.items.some((chunk) => chunk.chunk_id === chunkId)) return
    const next = chunkPage.offset + CHUNK_PAGE_SIZE
    if (next < chunkPage.total && next < MAX_SEEK_PAGES * CHUNK_PAGE_SIZE) setChunkOffset(next)
    else setPendingTarget(null)
  }, [pendingTarget, chunkPage, chunkOffset])

  useEffect(() => {
    if (!pendingTarget) return
    // The element only exists once its tab's data has landed, so this re-runs on arrival.
    const ready = tab === 'sections' ? sections !== undefined : chunkPage !== undefined
    if (!ready) return
    const element = document.getElementById(pendingTarget)
    if (!element) return

    element.scrollIntoView({ block: 'center', behavior: 'smooth' })
    setHighlighted(pendingTarget)
    setPendingTarget(null)
    window.clearTimeout(highlightTimer.current)
    highlightTimer.current = window.setTimeout(() => setHighlighted(null), HIGHLIGHT_MS)
  }, [pendingTarget, tab, sections, chunkPage])

  if (detail.isLoading) {
    return (
      <div className="page doc">
        <div className="doc__head-skeleton">
          <Skeleton width="18rem" height="1.75rem" />
          <Skeleton width="12rem" height="1rem" />
        </div>
        <Skeleton width="100%" height="2.5rem" radius="10px" />
        <PanelSkeleton />
      </div>
    )
  }

  if (detail.isError || !doc) {
    const notFound = detail.error instanceof ApiError && detail.error.status === 404
    return (
      <div className="page doc">
        {notFound ? (
          <EmptyState
            icon="document"
            title={t('document.notFound')}
            action={
              <Button icon="library" onClick={() => navigate('/library')}>
                {t('nav.library')}
              </Button>
            }
          />
        ) : (
          <PanelError error={detail.error} onRetry={() => void detail.refetch()} />
        )}
      </div>
    )
  }

  const tabs: TabItem<DocumentTab>[] = [
    { id: 'overview', label: t('document.overview') },
    { id: 'sections', label: t('document.sections'), count: sections?.length },
    { id: 'chunks', label: t('document.chunks'), count: doc.chunk_count },
    { id: 'entities', label: t('document.entities'), count: entities?.length },
    { id: 'source', label: t('document.source') },
  ]

  const onReindex = () => {
    if (!documentId) return
    reindex.mutate(documentId, {
      onSuccess: () => toast.success(t('library.reindexStarted')),
      onError: (error) => toast.error(t('errors.title'), errorBody(error)),
    })
  }

  const onDelete = () => {
    if (!documentId) return
    setConfirmOpen(false)
    remove.mutate(documentId, {
      onSuccess: () => {
        toast.success(t('library.deleted'))
        navigate('/library')
      },
      onError: (error) => toast.error(t('errors.title'), errorBody(error)),
    })
  }

  return (
    <div className="page doc">
      <header className="page__head">
        <div className="doc__identity">
          <div className="doc__title-row">
            <h1 className="page__title">{doc.title || doc.filename}</h1>
            <StatusPill status={doc.status} />
          </div>
          <p className="page__subtitle doc__filename" dir="auto">
            {doc.filename}
            <span className="doc__dot">·</span>
            {formatBytes(doc.size_bytes, language)}
          </p>
        </div>

        <div className="doc__actions">
          <Button
            variant="primary"
            icon="chat"
            onClick={() => navigate(`/chat?document=${encodeURIComponent(doc.id)}`)}
          >
            {t('document.askAbout')}
          </Button>
          <Button
            icon="refresh"
            loading={reindex.isPending}
            disabled={isInFlight(doc.status)}
            onClick={onReindex}
          >
            {t('library.reindex')}
          </Button>
          <Button
            variant="danger"
            icon="trash"
            loading={remove.isPending}
            onClick={() => setConfirmOpen(true)}
          >
            {t('common.delete')}
          </Button>
        </div>
      </header>

      <Tabs items={tabs} active={tab} onChange={setTab} />

      <div className="doc-panel">
        {tab === 'overview' && <OverviewPanel doc={doc} />}
        {tab === 'sections' && (
          <SectionsPanel
            sections={sections}
            isLoading={sectionsQuery.isLoading}
            error={sectionsQuery.error}
            onRetry={() => void sectionsQuery.refetch()}
            highlighted={highlighted}
          />
        )}
        {tab === 'chunks' && (
          <ChunksPanel
            page={chunkPage}
            isLoading={chunksQuery.isLoading}
            error={chunksQuery.error}
            onRetry={() => void chunksQuery.refetch()}
            offset={chunkOffset}
            onOffset={setChunkOffset}
            highlighted={highlighted}
          />
        )}
        {tab === 'entities' && (
          <EntitiesPanel
            entities={entities}
            isLoading={entitiesQuery.isLoading}
            error={entitiesQuery.error}
            onRetry={() => void entitiesQuery.refetch()}
            kind={kind}
            kinds={kinds}
            onKind={setKind}
          />
        )}
        {tab === 'source' && (
          <SourcePanel
            raw={rawQuery.data}
            isLoading={rawQuery.isLoading}
            error={rawQuery.error}
            onRetry={() => void rawQuery.refetch()}
          />
        )}
      </div>

      <ConfirmDialog
        open={confirmOpen}
        title={t('common.delete')}
        message={t('library.deleteConfirm')}
        confirmLabel={t('common.delete')}
        destructive
        onConfirm={onDelete}
        onCancel={() => setConfirmOpen(false)}
      />
    </div>
  )
}
