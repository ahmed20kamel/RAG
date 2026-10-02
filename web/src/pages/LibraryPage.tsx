import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Icon, type IconName } from '@/components/ui/Icon'
import { Button, Card, EmptyState, ErrorState, Field, IconButton, SearchField, Select, Skeleton } from '@/components/ui/primitives'
import { cx } from '@/utils/cx'
import { ConfirmDialog } from '@/components/ui/Modal'
import { StatusPill } from '@/components/documents/StatusPill'
import '@/components/documents/documents.css'
import {
  useCategories,
  useDeleteDocument,
  useDocumentList,
  useLibraryStats,
  useProjects,
  useReindexDocument,
} from '@/hooks/useDocuments'
import { useHealth } from '@/hooks/useSystem'
import { useDebounced } from '@/hooks/useDebounced'
import { useIsMobile } from '@/hooks/useMediaQuery'
import { useTranslation } from '@/hooks/useTranslation'
import { toast } from '@/state/toasts'
import { formatBytes, formatDate, formatDateTime, formatNumber, formatRelative, fileExtension } from '@/utils/format'
import type { DocumentQuery } from '@/services/documents'
import { PIPELINE_STAGES, type DocumentResponse, type DocumentStatus } from '@/types/api'

const PAGE_SIZE = 25
const STATUS_OPTIONS: DocumentStatus[] = [...PIPELINE_STAGES, 'failed']

type Tone = 'accent' | 'success' | 'warning' | 'danger'

function StatTile({
  label,
  value,
  icon,
  tone,
  loading,
  text,
}: {
  label: string
  value: string
  icon: IconName
  tone?: Tone
  loading?: boolean
  text?: boolean
}) {
  return (
    <Card className={cx('stat-tile', tone && `stat-tile--${tone}`)}>
      <div className="stat-tile__head">
        <span className="stat-tile__label">{label}</span>
        <Icon name={icon} size={15} className="stat-tile__icon" />
      </div>
      {loading ? (
        <Skeleton width="4.5rem" height="1.5rem" />
      ) : (
        <span className={cx('stat-tile__value', text && 'stat-tile__value--text')}>{value}</span>
      )}
    </Card>
  )
}

function LibraryStatsRow() {
  const { t, language } = useTranslation()
  const stats = useLibraryStats()
  const health = useHealth()

  const loadingStats = stats.isLoading
  const loadingHealth = health.isLoading
  const knowledge = health.data?.knowledge_index

  return (
    <div className="stats-grid">
      <StatTile
        label={t('library.documents')}
        icon="document"
        loading={loadingStats}
        value={formatNumber(stats.data?.documents ?? 0, language)}
      />
      <StatTile
        label={t('library.chunks')}
        icon="layers"
        loading={loadingStats}
        value={formatNumber(stats.data?.chunks ?? 0, language)}
      />
      <StatTile
        label={t('library.entities')}
        icon="tag"
        loading={loadingHealth}
        value={formatNumber(knowledge?.entities ?? 0, language)}
      />
      <StatTile
        label={t('library.sections')}
        icon="library"
        loading={loadingHealth}
        value={formatNumber(knowledge?.sections ?? 0, language)}
      />
      <StatTile
        label={t('library.indexStatus')}
        icon="shield"
        loading={loadingHealth}
        text
        tone={health.data?.status === 'ok' ? 'success' : 'warning'}
        value={health.data?.status === 'ok' ? t('library.indexed') : t('library.degraded')}
      />
      <StatTile
        label={t('library.lastIngestion')}
        icon="clock"
        loading={loadingStats}
        text
        value={formatRelative(stats.data?.last_ingested_at, language)}
      />
      <StatTile
        label={t('library.processing')}
        icon="refresh"
        loading={loadingStats}
        tone={(stats.data?.processing ?? 0) > 0 ? 'accent' : undefined}
        value={formatNumber(stats.data?.processing ?? 0, language)}
      />
      <StatTile
        label={t('library.failures')}
        icon="alert"
        loading={loadingStats}
        tone={(stats.data?.failed ?? 0) > 0 ? 'danger' : undefined}
        value={formatNumber(stats.data?.failed ?? 0, language)}
      />
    </div>
  )
}

function typeLabel(filename: string): string {
  return fileExtension(filename).replace('.', '') || '—'
}

export function LibraryPage() {
  const { t, language } = useTranslation()
  const navigate = useNavigate()
  const isMobile = useIsMobile()

  const [search, setSearch] = useState('')
  const [category, setCategory] = useState('')
  const [project, setProject] = useState('')
  const [status, setStatus] = useState<DocumentStatus | ''>('')
  const [offset, setOffset] = useState(0)
  const [pendingDelete, setPendingDelete] = useState<DocumentResponse | null>(null)

  const debouncedSearch = useDebounced(search, 300)
  const categories = useCategories()
  const projects = useProjects()
  const reindex = useReindexDocument()
  const remove = useDeleteDocument()

  // A narrowed filter can leave the current page past the end of the new result set.
  useEffect(() => setOffset(0), [debouncedSearch, category, project, status])

  const query = useMemo<DocumentQuery>(
    () => ({
      search: debouncedSearch || undefined,
      category: category || undefined,
      project: project || undefined,
      status: status || undefined,
      limit: PAGE_SIZE,
      offset,
    }),
    [debouncedSearch, category, project, status, offset],
  )

  const list = useDocumentList(query)
  const documents = list.data?.items ?? []
  const total = list.data?.total ?? 0
  const filtered = Boolean(debouncedSearch || category || project || status)
  const entitiesHint = t('library.entitiesPerDocument')

  const open = (id: string) => navigate(`/documents/${id}`)

  const onReindex = (id: string) =>
    reindex.mutate(id, {
      onSuccess: () => toast.success(t('library.reindexStarted')),
      onError: (error) => toast.error(t('errors.title'), error.message),
    })

  const confirmDelete = () => {
    const target = pendingDelete
    setPendingDelete(null)
    if (!target) return
    remove.mutate(target.id, {
      onSuccess: () => toast.success(t('library.deleted'), target.filename),
      onError: (error) => toast.error(t('errors.title'), error.message),
    })
  }

  const actions = (doc: DocumentResponse) => (
    <>
      <IconButton icon="externalLink" label={t('common.open')} size="sm" onClick={() => open(doc.id)} />
      <IconButton
        icon="refresh"
        label={t('library.reindex')}
        size="sm"
        disabled={reindex.isPending && reindex.variables === doc.id}
        onClick={() => onReindex(doc.id)}
      />
      <IconButton
        icon="trash"
        label={t('common.delete')}
        size="sm"
        onClick={() => setPendingDelete(doc)}
      />
    </>
  )

  let body
  if (list.isError) {
    body = (
      <ErrorState
        title={t('errors.title')}
        body={list.error instanceof Error ? list.error.message : t('errors.generic')}
        action={
          <Button variant="secondary" icon="refresh" onClick={() => void list.refetch()}>
            {t('common.retry')}
          </Button>
        }
      />
    )
  } else if (list.isLoading) {
    body = (
      <div className="doc-table__skeletons">
        {Array.from({ length: 6 }, (_, index) => (
          <Skeleton key={index} height="2.25rem" />
        ))}
      </div>
    )
  } else if (documents.length === 0) {
    body = filtered ? (
      <EmptyState icon="search" title={t('library.empty')} />
    ) : (
      <EmptyState
        icon="inbox"
        title={t('library.emptyAll')}
        body={t('library.emptyAllBody')}
        action={
          <Button variant="primary" icon="upload" onClick={() => navigate('/upload')}>
            {t('nav.upload')}
          </Button>
        }
      />
    )
  } else if (isMobile) {
    body = (
      <div className="doc-cards">
        {documents.map((doc) => (
          <Card
            key={doc.id}
            className="doc-card"
            role="button"
            tabIndex={0}
            onClick={() => open(doc.id)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault()
                open(doc.id)
              }
            }}
          >
            <div className="doc-card__head">
              <span className="doc-card__title">
                {doc.filename}
                {doc.title && doc.title !== doc.filename && (
                  <span className="doc-card__subtitle">{doc.title}</span>
                )}
              </span>
              <StatusPill status={doc.status} />
            </div>

            <div className="doc-card__grid">
              <div>
                <span className="doc-card__label">{t('library.table.type')}</span>
                <span className="doc-card__value doc-table__type">{typeLabel(doc.filename)}</span>
              </div>
              <div>
                <span className="doc-card__label">{t('library.table.size')}</span>
                <span className="doc-card__value">{formatBytes(doc.size_bytes, language)}</span>
              </div>
              <div>
                <span className="doc-card__label">{t('library.table.chunks')}</span>
                <span className="doc-card__value">{formatNumber(doc.chunk_count, language)}</span>
              </div>
              <div>
                <span className="doc-card__label">{t('library.table.entities')}</span>
                <span className="doc-card__value doc-table__muted" title={entitiesHint}>
                  —
                </span>
              </div>
              <div>
                <span className="doc-card__label">{t('library.table.uploaded')}</span>
                <span className="doc-card__value" title={formatDateTime(doc.uploaded_at, language)}>
                  {formatDate(doc.uploaded_at, language)}
                </span>
              </div>
            </div>

            <div
              className="doc-card__actions"
              onClick={(event) => event.stopPropagation()}
              onKeyDown={(event) => event.stopPropagation()}
            >
              {actions(doc)}
            </div>
          </Card>
        ))}
      </div>
    )
  } else {
    body = (
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>{t('library.table.filename')}</th>
              <th>{t('library.table.type')}</th>
              <th>{t('library.table.size')}</th>
              <th>{t('library.table.status')}</th>
              <th>{t('library.table.chunks')}</th>
              <th>{t('library.table.entities')}</th>
              <th>{t('library.table.uploaded')}</th>
              <th>{t('library.table.actions')}</th>
            </tr>
          </thead>
          <tbody>
            {documents.map((doc) => (
              <tr key={doc.id} className="doc-table__row" onClick={() => open(doc.id)}>
                <td>
                  <div className="doc-table__file">
                    <Icon name="file" size={16} className="doc-table__icon" />
                    <span className="doc-table__name">
                      <strong title={doc.filename}>{doc.filename}</strong>
                      {doc.title && doc.title !== doc.filename && <span>{doc.title}</span>}
                    </span>
                  </div>
                </td>
                <td>
                  <span className="doc-table__type">{typeLabel(doc.filename)}</span>
                </td>
                <td className="table__numeric">{formatBytes(doc.size_bytes, language)}</td>
                <td>
                  <StatusPill status={doc.status} />
                </td>
                <td className="table__numeric">{formatNumber(doc.chunk_count, language)}</td>
                <td className="doc-table__muted" title={entitiesHint}>
                  —
                </td>
                <td className="table__numeric" title={formatDateTime(doc.uploaded_at, language)}>
                  {formatDate(doc.uploaded_at, language)}
                </td>
                <td>
                  <div className="doc-table__actions" onClick={(event) => event.stopPropagation()}>
                    {actions(doc)}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )
  }


  const showCount = !list.isError && documents.length > 0
  const showPager = total > PAGE_SIZE

  return (
    <div className="page">
      <header className="page__head">
        <div>
          <h1 className="page__title">{t('library.title')}</h1>
          <p className="page__subtitle">{t('library.subtitle')}</p>
        </div>
        <Button variant="primary" icon="upload" onClick={() => navigate('/upload')}>
          {t('nav.upload')}
        </Button>
      </header>

      <LibraryStatsRow />

      <div className="library-filters">
        <div className="library-filters__search">
          <SearchField
            value={search}
            placeholder={t('library.searchPlaceholder')}
            aria-label={t('common.search')}
            onChange={(event) => setSearch(event.target.value)}
          />
        </div>
        {(projects.data?.length ?? 0) > 0 && (
          <div className="library-filters__select">
            <Field label={t('library.projects')} htmlFor="library-project">
              <Select id="library-project" value={project} onChange={(event) => setProject(event.target.value)}>
                <option value="">{t('library.allProjects')}</option>
                {(projects.data ?? []).map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </Select>
            </Field>
          </div>
        )}
        <div className="library-filters__select">
          <Field label={t('library.categories')} htmlFor="library-category">
            <Select
              id="library-category"
              value={category}
              onChange={(event) => setCategory(event.target.value)}
            >
              <option value="">{t('library.allCategories')}</option>
              {(categories.data ?? []).map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </Select>
          </Field>
        </div>
        <div className="library-filters__select">
          <Field label={t('library.table.status')} htmlFor="library-status">
            <Select
              id="library-status"
              value={status}
              onChange={(event) => setStatus(event.target.value as DocumentStatus | '')}
            >
              <option value="">{t('library.allStatuses')}</option>
              {STATUS_OPTIONS.map((option) => (
                <option key={option} value={option}>
                  {t(`status.${option}`)}
                </option>
              ))}
            </Select>
          </Field>
        </div>
      </div>

      {isMobile && !list.isError && documents.length > 0 ? (
        body
      ) : (
        <Card className="doc-table">{body}</Card>
      )}

      {showCount && (
        <div className="library-pager">
          <span className="library-pager__count">
            {t('library.rowsShown', { shown: offset + documents.length, total })}
          </span>
          {showPager && (
            <div className="library-pager__buttons">
              <Button
                variant="secondary"
                size="sm"
                icon="chevronStart"
                disabled={offset === 0}
                onClick={() => setOffset((current) => Math.max(0, current - PAGE_SIZE))}
              >
                {t('library.previous')}
              </Button>
              <Button
                variant="secondary"
                size="sm"
                iconEnd="chevronEnd"
                disabled={offset + PAGE_SIZE >= total}
                onClick={() => setOffset((current) => current + PAGE_SIZE)}
              >
                {t('library.next')}
              </Button>
            </div>
          )}
        </div>
      )}

      <ConfirmDialog
        open={pendingDelete !== null}
        title={t('common.delete')}
        message={t('library.deleteConfirm')}
        confirmLabel={t('common.delete')}
        destructive
        onConfirm={confirmDelete}
        onCancel={() => setPendingDelete(null)}
      />
    </div>
  )
}
