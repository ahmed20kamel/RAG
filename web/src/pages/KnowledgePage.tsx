import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { Icon, type IconName } from '@/components/ui/Icon'
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  Field,
  IconButton,
  Input,
  SearchField,
  Segmented,
  Select,
  Skeleton,
  Tabs,
  Textarea,
  type BadgeTone,
} from '@/components/ui/primitives'
import { Modal } from '@/components/ui/Modal'
import { cx } from '@/utils/cx'
import { can, useCurrentUser } from '@/hooks/useAuth'
import { useDebounced } from '@/hooks/useDebounced'
import { useIsMobile } from '@/hooks/useMediaQuery'
import { useTranslation } from '@/hooks/useTranslation'
import {
  useEditKnowledge,
  useKnowledgeItem,
  useKnowledgeList,
  useKnowledgeStats,
  useKnowledgeTransition,
  useTeachKnowledge,
} from '@/hooks/useKnowledge'
import { toast } from '@/state/toasts'
import { formatDate, formatDateTime, formatNumber, formatPercent } from '@/utils/format'
import type { KnowledgeQuery, KnowledgeTransition } from '@/services/knowledge'
import {
  FACTUAL_TYPES,
  KNOWLEDGE_SCOPES,
  KNOWLEDGE_STATUSES,
  KNOWLEDGE_TYPES,
  REVIEW_DECISIONS,
  type KnowledgeDetail,
  type KnowledgeItem,
  type KnowledgeScope,
  type KnowledgeStatus,
  type KnowledgeType,
  type ReviewDecision,
} from '@/types/knowledge'
import './knowledge.css'

const PAGE_SIZE = 25
const MAX_CONTENT = 4000
const MAX_TAGS = 12

type TabId = 'all' | 'pending' | 'approved' | 'active' | 'rejected' | 'archived'

const TABS: TabId[] = ['all', 'pending', 'approved', 'active', 'rejected', 'archived']

/** "Pending review" is two statuses: waiting for a reviewer, and one already reading it. */
const TAB_STATUSES: Record<TabId, KnowledgeStatus[]> = {
  all: [],
  pending: ['pending', 'in_review'],
  approved: ['approved'],
  active: ['active'],
  rejected: ['rejected'],
  archived: ['archived'],
}

/**
 * Each type gets its own colour and glyph, and the two families get different outlines,
 * so a rule can never be mistaken for a fact at a glance — which is the failure that
 * lets a style note overrule a contract figure.
 */
const TYPE_TONE: Record<KnowledgeType, BadgeTone> = {
  fact: 'info',
  correction: 'danger',
  procedure: 'accent',
  terminology: 'success',
  rule: 'warning',
  preference: 'neutral',
}

const TYPE_ICON: Record<KnowledgeType, IconName> = {
  fact: 'info',
  correction: 'alert',
  procedure: 'layers',
  terminology: 'tag',
  rule: 'shield',
  preference: 'sparkles',
}

/** The wider the reach, the louder the badge: a company-wide item touches everyone. */
const SCOPE_TONE: Record<KnowledgeScope, BadgeTone> = {
  user: 'neutral',
  team: 'info',
  department: 'accent',
  global: 'warning',
}

const STATUS_TONE: Record<KnowledgeStatus, BadgeTone> = {
  draft: 'neutral',
  pending: 'warning',
  in_review: 'info',
  approved: 'accent',
  active: 'success',
  rejected: 'danger',
  archived: 'neutral',
}

/** Mirrors `TRANSITIONS` in `app/core/knowledge.py`; the server refuses anything else. */
const STATUS_ACTIONS: Record<KnowledgeStatus, KnowledgeTransition[]> = {
  draft: ['submit', 'archive'],
  pending: ['review', 'approve', 'reject', 'archive'],
  in_review: ['approve', 'reject', 'archive'],
  approved: ['activate', 'archive'],
  active: ['archive'],
  rejected: ['submit', 'archive'],
  archived: ['restore'],
}

/** Which server-computed flag each decision rides on. The role is never consulted here. */
const ACTION_FLAG: Record<KnowledgeTransition, 'can_edit' | 'can_approve' | 'can_archive'> = {
  submit: 'can_edit',
  review: 'can_approve',
  approve: 'can_approve',
  activate: 'can_approve',
  reject: 'can_approve',
  archive: 'can_archive',
  restore: 'can_archive',
}

const ACTION_ICON: Record<KnowledgeTransition, IconName> = {
  submit: 'send',
  review: 'search',
  approve: 'check',
  activate: 'sparkles',
  reject: 'close',
  archive: 'archive',
  restore: 'refresh',
}

function allowedActions(item: KnowledgeItem): KnowledgeTransition[] {
  return STATUS_ACTIONS[item.status].filter((action) => item[ACTION_FLAG[action]])
}

/* Badges ------------------------------------------------------------------ */

function TypeBadge({ type }: { type: KnowledgeType }) {
  const { t } = useTranslation()
  const factual = FACTUAL_TYPES.includes(type)
  return (
    <Badge
      tone={TYPE_TONE[type]}
      className={cx('k-type', factual ? 'k-type--factual' : 'k-type--behavioural')}
      title={t(factual ? 'knowledge.groups.factual' : 'knowledge.groups.behavioural')}
    >
      <Icon name={TYPE_ICON[type]} size={12} />
      {t(`knowledge.types.${type}`)}
    </Badge>
  )
}

function ScopeBadge({ scope }: { scope: KnowledgeScope }) {
  const { t } = useTranslation()
  return <Badge tone={SCOPE_TONE[scope]}>{t(`knowledge.scopes.${scope}`)}</Badge>
}

function StatusBadge({ status }: { status: KnowledgeStatus }) {
  const { t } = useTranslation()
  return (
    <Badge tone={STATUS_TONE[status]} dot>
      {t(`knowledge.statuses.${status}`)}
    </Badge>
  )
}

function StatTile({
  label,
  value,
  icon,
  tone,
  loading,
}: {
  label: string
  value: string
  icon: IconName
  tone?: 'accent' | 'success' | 'warning'
  loading?: boolean
}) {
  return (
    <Card className={cx('k-tile', tone && `k-tile--${tone}`)}>
      <div className="k-tile__head">
        <span className="k-tile__label">{label}</span>
        <Icon name={icon} size={15} className="k-tile__icon" />
      </div>
      {loading ? <Skeleton width="3.5rem" height="1.5rem" /> : <span className="k-tile__value">{value}</span>}
    </Card>
  )
}

/* Teach ------------------------------------------------------------------- */

function TeachDialog({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation()
  const teach = useTeachKnowledge()

  const [type, setType] = useState<KnowledgeType>('fact')
  const [content, setContent] = useState('')
  const [scope, setScope] = useState<KnowledgeScope>('user')
  const [sourceText, setSourceText] = useState('')
  const [confidence, setConfidence] = useState(0.5)
  const [explanation, setExplanation] = useState('')
  const [tags, setTags] = useState('')

  const valid = content.trim().length > 0

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!valid) return
    teach.mutate(
      {
        type,
        content: content.trim(),
        scope,
        source_text: sourceText.trim(),
        confidence,
        explanation: explanation.trim(),
        tags: tags
          .split(',')
          .map((tag) => tag.trim())
          .filter(Boolean)
          .slice(0, MAX_TAGS),
      },
      {
        onSuccess: () => {
          toast.success(t('knowledge.taught'))
          onClose()
        },
        onError: (error: Error) => toast.error(t('errors.title'), error.message),
      },
    )
  }

  return (
    <Modal
      open
      title={t('knowledge.teach')}
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button
            type="submit"
            form="knowledge-teach"
            variant="primary"
            loading={teach.isPending}
            disabled={!valid}
          >
            {t('knowledge.teach')}
          </Button>
        </>
      }
    >
      <form id="knowledge-teach" className="k-form" onSubmit={submit}>
        <div className="k-form__row">
          <Field label={t('knowledge.form.type')} htmlFor="teach-type">
            <Select
              id="teach-type"
              value={type}
              onChange={(event) => setType(event.target.value as KnowledgeType)}
            >
              {KNOWLEDGE_TYPES.map((value) => (
                <option key={value} value={value}>
                  {t(`knowledge.types.${value}`)}
                </option>
              ))}
            </Select>
          </Field>

          <Field label={t('knowledge.form.scope')} htmlFor="teach-scope">
            <Select
              id="teach-scope"
              value={scope}
              onChange={(event) => setScope(event.target.value as KnowledgeScope)}
            >
              {KNOWLEDGE_SCOPES.map((value) => (
                <option key={value} value={value}>
                  {t(`knowledge.scopes.${value}`)}
                </option>
              ))}
            </Select>
          </Field>
        </div>

        <p className="k-form__note">{t('knowledge.groups.note')}</p>

        <Field label={t('knowledge.form.content')} htmlFor="teach-content">
          <Textarea
            id="teach-content"
            rows={4}
            required
            autoFocus
            maxLength={MAX_CONTENT}
            placeholder={t('knowledge.form.contentPlaceholder')}
            value={content}
            onChange={(event) => setContent(event.target.value)}
          />
        </Field>

        <Field label={t('knowledge.form.sourceText')} hint={t('knowledge.form.sourceTextHint')} htmlFor="teach-source">
          <Input
            id="teach-source"
            maxLength={2000}
            value={sourceText}
            onChange={(event) => setSourceText(event.target.value)}
          />
        </Field>

        <Field label={t('knowledge.form.confidence')} hint={t('knowledge.form.confidenceHint')} htmlFor="teach-confidence">
          <div className="k-range">
            <input
              id="teach-confidence"
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={confidence}
              onChange={(event) => setConfidence(Number(event.target.value))}
            />
            <output className="k-range__value" htmlFor="teach-confidence">
              {confidence.toFixed(2)}
            </output>
          </div>
        </Field>

        <Field label={t('knowledge.form.explanation')} hint={t('knowledge.form.explanationHint')} htmlFor="teach-explanation">
          <Textarea
            id="teach-explanation"
            rows={3}
            maxLength={2000}
            value={explanation}
            onChange={(event) => setExplanation(event.target.value)}
          />
        </Field>

        <Field label={t('knowledge.form.tags')} hint={t('knowledge.form.tagsHint')} htmlFor="teach-tags">
          <Input id="teach-tags" value={tags} onChange={(event) => setTags(event.target.value)} />
        </Field>
      </form>
    </Modal>
  )
}

/* Detail ------------------------------------------------------------------ */

function EditForm({
  item,
  pending,
  onCancel,
  onSubmit,
}: {
  item: KnowledgeDetail
  pending: boolean
  onCancel: () => void
  onSubmit: (body: {
    content: string
    change_reason: string
    source_text: string
    confidence: number
    explanation: string
  }) => void
}) {
  const { t } = useTranslation()
  const [content, setContent] = useState(item.content)
  const [changeReason, setChangeReason] = useState('')
  const [sourceText, setSourceText] = useState(item.source_text)
  const [confidence, setConfidence] = useState(item.confidence)
  const [explanation, setExplanation] = useState(item.explanation)

  const valid = content.trim().length > 0

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!valid) return
    onSubmit({
      content: content.trim(),
      change_reason: changeReason.trim(),
      source_text: sourceText.trim(),
      confidence,
      explanation: explanation.trim(),
    })
  }

  return (
    <form className="k-form" onSubmit={submit}>
      <Field label={t('knowledge.form.content')} htmlFor="edit-content">
        <Textarea
          id="edit-content"
          rows={4}
          required
          autoFocus
          maxLength={MAX_CONTENT}
          value={content}
          onChange={(event) => setContent(event.target.value)}
        />
      </Field>

      <Field label={t('knowledge.form.changeReason')} hint={t('knowledge.form.changeReasonHint')} htmlFor="edit-reason">
        <Input
          id="edit-reason"
          maxLength={1000}
          value={changeReason}
          onChange={(event) => setChangeReason(event.target.value)}
        />
      </Field>

      <Field label={t('knowledge.form.sourceText')} htmlFor="edit-source">
        <Input
          id="edit-source"
          maxLength={2000}
          value={sourceText}
          onChange={(event) => setSourceText(event.target.value)}
        />
      </Field>

      <Field label={t('knowledge.form.confidence')} htmlFor="edit-confidence">
        <div className="k-range">
          <input
            id="edit-confidence"
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={confidence}
            onChange={(event) => setConfidence(Number(event.target.value))}
          />
          <output className="k-range__value" htmlFor="edit-confidence">
            {confidence.toFixed(2)}
          </output>
        </div>
      </Field>

      <Field label={t('knowledge.form.explanation')} htmlFor="edit-explanation">
        <Textarea
          id="edit-explanation"
          rows={3}
          maxLength={2000}
          value={explanation}
          onChange={(event) => setExplanation(event.target.value)}
        />
      </Field>

      <div className="k-form__actions">
        <Button variant="ghost" type="button" onClick={onCancel}>
          {t('common.cancel')}
        </Button>
        <Button variant="primary" type="submit" loading={pending} disabled={!valid}>
          {t('common.save')}
        </Button>
      </div>
    </form>
  )
}

function DetailDialog({
  id,
  startEditing,
  onClose,
}: {
  id: string
  startEditing: boolean
  onClose: () => void
}) {
  const { t, language } = useTranslation()
  const query = useKnowledgeItem(id)
  const edit = useEditKnowledge()
  const transition = useKnowledgeTransition()

  const [editing, setEditing] = useState(startEditing)
  const [reason, setReason] = useState('')

  const item = query.data
  const statusLabel = (value: string) =>
    KNOWLEDGE_STATUSES.includes(value as KnowledgeStatus)
      ? t(`knowledge.statuses.${value as KnowledgeStatus}`)
      : value
  const decisionLabel = (value: string) =>
    (REVIEW_DECISIONS as readonly string[]).includes(value)
      ? t(`knowledge.decisions.${value as ReviewDecision}`)
      : value

  const decide = (action: KnowledgeTransition) => {
    // The backend refuses a rejection with no reason, so it is stopped here rather than
    // sent and bounced back as a validation error.
    if (action === 'reject' && !reason.trim()) {
      toast.warning(t('knowledge.decide.reasonRequired'))
      return
    }
    transition.mutate(
      { id, action, reason: reason.trim() },
      {
        onSuccess: () => {
          setReason('')
          toast.success(t('knowledge.decide.applied'), t(`knowledge.actions.${action}`))
        },
        onError: (error: Error) => toast.error(t('errors.title'), error.message),
      },
    )
  }

  const save = (body: Parameters<Parameters<typeof EditForm>[0]['onSubmit']>[0]) =>
    edit.mutate(
      { id, body },
      {
        onSuccess: () => {
          setEditing(false)
          toast.success(t('knowledge.edit.saved'))
        },
        onError: (error: Error) => toast.error(t('errors.title'), error.message),
      },
    )

  let body
  if (query.isError) {
    body = (
      <ErrorState
        title={t('errors.title')}
        body={query.error instanceof Error ? query.error.message : t('errors.generic')}
        action={
          <Button variant="secondary" icon="refresh" onClick={() => void query.refetch()}>
            {t('common.retry')}
          </Button>
        }
      />
    )
  } else if (!item) {
    body = (
      <div className="k-detail__skeletons">
        {Array.from({ length: 5 }, (_, index) => (
          <Skeleton key={index} height="2rem" />
        ))}
      </div>
    )
  } else {
    const actions = allowedActions(item)
    body = (
      <div className="k-detail">
        <div className="k-detail__badges">
          <TypeBadge type={item.type} />
          <ScopeBadge scope={item.scope} />
          <StatusBadge status={item.status} />
          <span className="k-detail__version">v{formatNumber(item.version_no, language)}</span>
          {item.can_edit && !editing && (
            <Button size="sm" variant="ghost" icon="settings" onClick={() => setEditing(true)}>
              {t('knowledge.actions.edit')}
            </Button>
          )}
        </div>

        {editing ? (
          <EditForm item={item} pending={edit.isPending} onCancel={() => setEditing(false)} onSubmit={save} />
        ) : (
          <p className="k-detail__content">{item.content}</p>
        )}

        <dl className="k-detail__grid">
          <div>
            <dt>{t('knowledge.detail.owner')}</dt>
            <dd>{item.created_by_name || t('knowledge.detail.unknownOwner')}</dd>
          </div>
          <div>
            <dt>{t('knowledge.detail.approver')}</dt>
            <dd>{item.approved_by_name || '—'}</dd>
          </div>
          <div>
            <dt>{t('knowledge.detail.confidence')}</dt>
            <dd className="ltr-nums">{formatPercent(item.confidence, language)}</dd>
          </div>
          <div>
            <dt>{t('knowledge.detail.createdAt')}</dt>
            <dd className="ltr-nums">{formatDateTime(item.created_at, language)}</dd>
          </div>
          <div>
            <dt>{t('knowledge.detail.updatedAt')}</dt>
            <dd className="ltr-nums">{formatDateTime(item.updated_at, language)}</dd>
          </div>
          <div>
            <dt>{t('knowledge.detail.approvedAt')}</dt>
            <dd className="ltr-nums">{formatDateTime(item.approved_at, language)}</dd>
          </div>
          <div>
            <dt>{t('knowledge.detail.activatedAt')}</dt>
            <dd className="ltr-nums">{formatDateTime(item.activated_at, language)}</dd>
          </div>
          <div>
            <dt>{t('knowledge.detail.archivedAt')}</dt>
            <dd className="ltr-nums">{formatDateTime(item.archived_at, language)}</dd>
          </div>
        </dl>

        <section className="k-section">
          <h3 className="k-section__title">{t('knowledge.detail.source')}</h3>
          {item.source_text ? (
            <blockquote className="k-quote">{item.source_text}</blockquote>
          ) : (
            <p className="k-section__empty">{t('knowledge.detail.noSource')}</p>
          )}
          {item.source_document_id && (
            <Link className="k-link" to={`/documents/${item.source_document_id}`} onClick={onClose}>
              <Icon name="externalLink" size={13} />
              {t('knowledge.detail.openDocument')}
            </Link>
          )}
        </section>

        <section className="k-section">
          <h3 className="k-section__title">{t('knowledge.detail.explanation')}</h3>
          <p className={cx(!item.explanation && 'k-section__empty')}>
            {item.explanation || t('knowledge.detail.noExplanation')}
          </p>
        </section>

        <section className="k-section">
          <h3 className="k-section__title">{t('knowledge.detail.tags')}</h3>
          {item.tags.length > 0 ? (
            <div className="k-tags">
              {item.tags.map((tag) => (
                <Badge key={tag} tone="neutral">
                  {tag}
                </Badge>
              ))}
            </div>
          ) : (
            <p className="k-section__empty">{t('knowledge.detail.noTags')}</p>
          )}
        </section>

        {actions.length > 0 && (
          <section className="k-section">
            <h3 className="k-section__title">{t('knowledge.decide.title')}</h3>
            <Field label={t('knowledge.decide.reason')} htmlFor="decide-reason">
              <Textarea
                id="decide-reason"
                rows={2}
                maxLength={1000}
                placeholder={t('knowledge.decide.reasonPlaceholder')}
                value={reason}
                onChange={(event) => setReason(event.target.value)}
              />
            </Field>
            <div className="k-decide__buttons">
              {actions.map((action) => (
                <Button
                  key={action}
                  size="sm"
                  variant={action === 'reject' ? 'danger' : action === 'archive' ? 'secondary' : 'primary'}
                  icon={ACTION_ICON[action]}
                  loading={transition.isPending && transition.variables?.action === action}
                  disabled={transition.isPending}
                  onClick={() => decide(action)}
                >
                  {t(`knowledge.actions.${action}`)}
                </Button>
              ))}
            </div>
          </section>
        )}

        <section className="k-section">
          <h3 className="k-section__title">{t('knowledge.versions.title')}</h3>
          {item.versions.length > 0 ? (
            <ol className="k-trail">
              {item.versions.map((version) => (
                <li key={version.id} className="k-trail__row">
                  <span className="k-trail__mark">
                    {t('knowledge.versions.version', { n: formatNumber(version.version_no, language) })}
                  </span>
                  <span className="k-trail__body">
                    <span className="k-trail__text">{version.content}</span>
                    <span className="k-trail__meta">
                      {t('knowledge.versions.changeReason')}:{' '}
                      {version.change_reason || t('knowledge.versions.noReason')}
                    </span>
                  </span>
                  <span className="k-trail__date ltr-nums">{formatDateTime(version.created_at, language)}</span>
                </li>
              ))}
            </ol>
          ) : (
            <p className="k-section__empty">{t('knowledge.versions.empty')}</p>
          )}
        </section>

        <section className="k-section">
          <h3 className="k-section__title">{t('knowledge.audit.title')}</h3>
          {item.reviews.length > 0 ? (
            <ol className="k-trail">
              {item.reviews.map((review) => (
                <li key={review.id} className="k-trail__row">
                  <span className="k-trail__mark">{decisionLabel(review.decision)}</span>
                  <span className="k-trail__body">
                    <span className="k-trail__text">
                      {t('knowledge.audit.transition', {
                        from: statusLabel(review.from_status),
                        to: statusLabel(review.to_status),
                      })}
                    </span>
                    {review.reason && (
                      <span className="k-trail__meta">
                        {t('knowledge.audit.reason')}: {review.reason}
                      </span>
                    )}
                  </span>
                  <span className="k-trail__date ltr-nums">{formatDateTime(review.created_at, language)}</span>
                </li>
              ))}
            </ol>
          ) : (
            <p className="k-section__empty">{t('knowledge.audit.empty')}</p>
          )}
        </section>

        <section className="k-section">
          <h3 className="k-section__title">{t('knowledge.usage.title')}</h3>
          <p className="k-section__lead">
            {t('knowledge.usage.count', { count: formatNumber(item.usage_count, language) })}
          </p>
          {item.usages.length > 0 ? (
            <ol className="k-trail">
              {item.usages.map((usage) => (
                <li key={usage.id} className="k-trail__row">
                  <span className="k-trail__mark ltr-nums">{usage.question_hash.slice(0, 10)}</span>
                  <span className="k-trail__body">
                    <span className="k-trail__text">{usage.influence || '—'}</span>
                    <Badge tone={usage.conflicted ? 'danger' : 'neutral'}>
                      {usage.conflicted ? t('knowledge.conflict.yes') : t('knowledge.conflict.no')}
                    </Badge>
                  </span>
                  <span className="k-trail__date ltr-nums">{formatDateTime(usage.used_at, language)}</span>
                </li>
              ))}
            </ol>
          ) : (
            <p className="k-section__empty">{t('knowledge.usage.empty')}</p>
          )}
          <p className="k-section__note">
            {t('knowledge.usage.note')} {t('knowledge.conflict.note')}
          </p>
        </section>
      </div>
    )
  }

  return (
    <Modal open wide title={t('knowledge.detail.title')} onClose={onClose}>
      {body}
    </Modal>
  )
}

/* Page -------------------------------------------------------------------- */

export function KnowledgePage() {
  const { t, language } = useTranslation()
  const { data: user } = useCurrentUser()
  const isMobile = useIsMobile()

  const [tab, setTab] = useState<TabId>('all')
  const [search, setSearch] = useState('')
  const [type, setType] = useState<KnowledgeType | ''>('')
  const [scope, setScope] = useState<KnowledgeScope | ''>('')
  const [owner, setOwner] = useState<'everyone' | 'mine'>('everyone')
  const [offset, setOffset] = useState(0)
  const [teaching, setTeaching] = useState(false)
  const [opened, setOpened] = useState<{ id: string; editing: boolean } | null>(null)

  const debouncedSearch = useDebounced(search, 300)
  const allowed = can(user, 'knowledge.read')

  // A narrowed filter can leave the current page past the end of the new result set.
  useEffect(() => setOffset(0), [debouncedSearch, type, scope, owner, tab])

  const query = useMemo<KnowledgeQuery>(
    () => ({
      q: debouncedSearch.trim() || undefined,
      type: type ? [type] : undefined,
      scope: scope ? [scope] : undefined,
      status: TAB_STATUSES[tab].length > 0 ? TAB_STATUSES[tab] : undefined,
      mine: owner === 'mine' || undefined,
      limit: PAGE_SIZE,
      offset,
    }),
    [debouncedSearch, type, scope, tab, owner, offset],
  )

  const list = useKnowledgeList(query, allowed)
  const stats = useKnowledgeStats(allowed)
  const transition = useKnowledgeTransition()

  // The server enforces this as well; refusing here only avoids a page that would 403.
  if (!allowed) {
    return (
      <div className="page">
        <ErrorState title={t('auth.forbidden')} />
      </div>
    )
  }

  const items = list.data?.items ?? []
  const total = list.data?.total ?? 0
  const byStatus = stats.data?.by_status ?? {}
  const byType = stats.data?.by_type ?? {}

  const tabCount = (id: TabId): number | undefined => {
    if (!stats.data) return undefined
    if (id === 'all') return stats.data.total
    if (id === 'pending') return stats.data.pending_review
    if (id === 'active') return stats.data.active
    return byStatus[id] ?? 0
  }

  const quickDecide = (item: KnowledgeItem, action: KnowledgeTransition) =>
    transition.mutate(
      { id: item.id, action },
      {
        onSuccess: () => toast.success(t('knowledge.decide.applied'), t(`knowledge.actions.${action}`)),
        onError: (error: Error) => toast.error(t('errors.title'), error.message),
      },
    )

  const rowActions = (item: KnowledgeItem) => {
    const actions = allowedActions(item).filter(
      (action) => action === 'approve' || action === 'activate' || action === 'archive',
    )
    return (
      <>
        {item.can_edit && (
          <IconButton
            icon="settings"
            label={t('knowledge.actions.edit')}
            size="sm"
            onClick={(event) => {
              event.stopPropagation()
              setOpened({ id: item.id, editing: true })
            }}
          />
        )}
        {actions.map((action) => (
          <IconButton
            key={action}
            icon={ACTION_ICON[action]}
            label={t(`knowledge.actions.${action}`)}
            size="sm"
            disabled={transition.isPending}
            onClick={(event) => {
              event.stopPropagation()
              quickDecide(item, action)
            }}
          />
        ))}
      </>
    )
  }

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
      <div className="k-table__skeletons">
        {Array.from({ length: 6 }, (_, index) => (
          <Skeleton key={index} height="2.25rem" />
        ))}
      </div>
    )
  } else if (items.length === 0) {
    body = <EmptyState icon="inbox" title={t('knowledge.empty.title')} body={t('knowledge.empty.body')} />
  } else if (isMobile) {
    body = (
      <div className="k-cards">
        {items.map((item) => (
          <Card
            key={item.id}
            className="k-card"
            role="button"
            tabIndex={0}
            onClick={() => setOpened({ id: item.id, editing: false })}
            onKeyDown={(event) => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault()
                setOpened({ id: item.id, editing: false })
              }
            }}
          >
            <div className="k-card__badges">
              <TypeBadge type={item.type} />
              <ScopeBadge scope={item.scope} />
              <StatusBadge status={item.status} />
            </div>
            <p className="k-card__content">{item.content}</p>
            <div className="k-card__grid">
              <div>
                <span className="k-card__label">{t('knowledge.table.owner')}</span>
                <span className="k-card__value">{item.created_by_name || t('knowledge.detail.unknownOwner')}</span>
              </div>
              <div>
                <span className="k-card__label">{t('knowledge.table.version')}</span>
                <span className="k-card__value ltr-nums">v{formatNumber(item.version_no, language)}</span>
              </div>
              <div>
                <span className="k-card__label">{t('knowledge.table.updated')}</span>
                <span className="k-card__value ltr-nums">{formatDate(item.updated_at, language)}</span>
              </div>
            </div>
            <div className="k-card__actions">{rowActions(item)}</div>
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
              <th>{t('knowledge.table.content')}</th>
              <th>{t('knowledge.table.type')}</th>
              <th>{t('knowledge.table.scope')}</th>
              <th>{t('knowledge.table.status')}</th>
              <th>{t('knowledge.table.owner')}</th>
              <th>{t('knowledge.table.version')}</th>
              <th>{t('knowledge.table.updated')}</th>
              <th>{t('knowledge.table.actions')}</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.id} className="k-table__row" onClick={() => setOpened({ id: item.id, editing: false })}>
                <td>
                  <span className="k-table__content">{item.content}</span>
                </td>
                <td>
                  <TypeBadge type={item.type} />
                </td>
                <td>
                  <ScopeBadge scope={item.scope} />
                </td>
                <td>
                  <StatusBadge status={item.status} />
                </td>
                <td className="k-table__muted">{item.created_by_name || t('knowledge.detail.unknownOwner')}</td>
                <td className="table__numeric">v{formatNumber(item.version_no, language)}</td>
                <td className="table__numeric" title={formatDateTime(item.updated_at, language)}>
                  {formatDate(item.updated_at, language)}
                </td>
                <td>
                  <div className="k-table__actions">{rowActions(item)}</div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )
  }

  const showCount = !list.isError && items.length > 0

  return (
    <div className="page">
      <header className="page__head">
        <div>
          <h1 className="page__title">{t('knowledge.title')}</h1>
          <p className="page__subtitle">{t('knowledge.subtitle')}</p>
        </div>
        {can(user, 'knowledge.propose') && (
          <Button variant="primary" icon="plus" onClick={() => setTeaching(true)}>
            {t('knowledge.teach')}
          </Button>
        )}
      </header>

      <div className="k-stats">
        <StatTile
          label={t('knowledge.stats.total')}
          icon="database"
          loading={stats.isLoading}
          value={formatNumber(stats.data?.total ?? 0, language)}
        />
        <StatTile
          label={t('knowledge.stats.pendingReview')}
          icon="clock"
          tone={(stats.data?.pending_review ?? 0) > 0 ? 'warning' : undefined}
          loading={stats.isLoading}
          value={formatNumber(stats.data?.pending_review ?? 0, language)}
        />
        <StatTile
          label={t('knowledge.stats.active')}
          icon="check"
          tone="success"
          loading={stats.isLoading}
          value={formatNumber(stats.data?.active ?? 0, language)}
        />
        {KNOWLEDGE_TYPES.map((value) => (
          <StatTile
            key={value}
            label={t(`knowledge.types.${value}`)}
            icon={TYPE_ICON[value]}
            loading={stats.isLoading}
            value={formatNumber(byType[value] ?? 0, language)}
          />
        ))}
      </div>

      <Tabs
        items={TABS.map((id) => ({ id, label: t(`knowledge.tabs.${id}`), count: tabCount(id) }))}
        active={tab}
        onChange={setTab}
      />

      <div className="k-filters">
        <div className="k-filters__search">
          <SearchField
            value={search}
            placeholder={t('knowledge.filters.search')}
            aria-label={t('common.search')}
            onChange={(event) => setSearch(event.target.value)}
          />
        </div>
        <div className="k-filters__select">
          <Field label={t('knowledge.filters.type')} htmlFor="knowledge-type">
            <Select
              id="knowledge-type"
              value={type}
              onChange={(event) => setType(event.target.value as KnowledgeType | '')}
            >
              <option value="">{t('knowledge.filters.allTypes')}</option>
              {KNOWLEDGE_TYPES.map((value) => (
                <option key={value} value={value}>
                  {t(`knowledge.types.${value}`)}
                </option>
              ))}
            </Select>
          </Field>
        </div>
        <div className="k-filters__select">
          <Field label={t('knowledge.filters.scope')} htmlFor="knowledge-scope">
            <Select
              id="knowledge-scope"
              value={scope}
              onChange={(event) => setScope(event.target.value as KnowledgeScope | '')}
            >
              <option value="">{t('knowledge.filters.allScopes')}</option>
              {KNOWLEDGE_SCOPES.map((value) => (
                <option key={value} value={value}>
                  {t(`knowledge.scopes.${value}`)}
                </option>
              ))}
            </Select>
          </Field>
        </div>
        <div className="k-filters__owner">
          <Segmented
            label={t('knowledge.filters.audience')}
            value={owner}
            onChange={setOwner}
            options={[
              { value: 'everyone', label: t('knowledge.filters.everyone') },
              { value: 'mine', label: t('knowledge.filters.mine'), icon: 'shield' },
            ]}
          />
        </div>
      </div>

      {isMobile && !list.isError && items.length > 0 ? body : <Card className="k-table">{body}</Card>}

      {showCount && (
        <div className="k-pager">
          <span className="k-pager__count">
            {t('common.rowsShown', { shown: offset + items.length, total })}
          </span>
          {total > PAGE_SIZE && (
            <div className="k-pager__buttons">
              <Button
                variant="secondary"
                size="sm"
                icon="chevronStart"
                disabled={offset === 0}
                onClick={() => setOffset((current) => Math.max(0, current - PAGE_SIZE))}
              >
                {t('common.previous')}
              </Button>
              <Button
                variant="secondary"
                size="sm"
                iconEnd="chevronEnd"
                disabled={offset + PAGE_SIZE >= total}
                onClick={() => setOffset((current) => current + PAGE_SIZE)}
              >
                {t('common.next')}
              </Button>
            </div>
          )}
        </div>
      )}

      {teaching && <TeachDialog onClose={() => setTeaching(false)} />}

      {opened && (
        <DetailDialog
          key={opened.id}
          id={opened.id}
          startEditing={opened.editing}
          onClose={() => setOpened(null)}
        />
      )}
    </div>
  )
}
