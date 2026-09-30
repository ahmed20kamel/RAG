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
  Input,
  Select,
  Skeleton,
  Tabs,
  Textarea,
  type BadgeTone,
} from '@/components/ui/primitives'
import { Modal } from '@/components/ui/Modal'
import { WhyThisAnswer } from '@/components/chat/WhyThisAnswer'
import { cx } from '@/utils/cx'
import { can, useCurrentUser } from '@/hooks/useAuth'
import { useIsMobile } from '@/hooks/useMediaQuery'
import { useTranslation, type TranslationKey } from '@/hooks/useTranslation'
import {
  useAcceptCandidate,
  useCandidate,
  useCandidateList,
  useCandidateStats,
  useDismissCandidate,
} from '@/hooks/useCandidates'
import { toast } from '@/state/toasts'
import { formatDate, formatDateTime, formatNumber, formatPercent } from '@/utils/format'
import type { CandidateQuery } from '@/services/candidates'
import { CANDIDATE_STATES, type Candidate, type CandidateState } from '@/types/candidates'
import {
  FACTUAL_TYPES,
  KNOWLEDGE_SCOPES,
  KNOWLEDGE_TYPES,
  type KnowledgeScope,
  type KnowledgeType,
} from '@/types/knowledge'
import './candidates.css'

const PAGE_SIZE = 25
const MAX_CONTENT = 4000
const MAX_NOTE = 2000
const MAX_REASON = 1000
const MAX_TAGS = 12

/** Mirrors `KnowledgeType` on the Knowledge page, so one type reads the same everywhere. */
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

const SCOPE_TONE: Record<KnowledgeScope, BadgeTone> = {
  user: 'neutral',
  team: 'info',
  department: 'accent',
  global: 'warning',
}

/** Open is the only state that still asks something of the reader, so it is the loud one. */
const STATE_TONE: Record<CandidateState, BadgeTone> = {
  offered: 'warning',
  accepted: 'success',
  dismissed: 'neutral',
  rejected: 'danger',
}

const STATE_ICON: Record<CandidateState, IconName> = {
  offered: 'clock',
  accepted: 'check',
  dismissed: 'archive',
  rejected: 'close',
}

/**
 * The detector's signal names. It is a plain string on the wire, so an unknown one is
 * shown as it came rather than swallowed — a new cue should be visible, not invisible.
 */
const SIGNAL_KEYS: Record<string, TranslationKey> = {
  correction: 'candidates.signals.correction',
  explicit_correction: 'candidates.signals.explicit_correction',
  rule: 'candidates.signals.rule',
  procedure: 'candidates.signals.procedure',
  terminology: 'candidates.signals.terminology',
  preference: 'candidates.signals.preference',
  fact: 'candidates.signals.fact',
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

function StateBadge({ state }: { state: CandidateState }) {
  const { t } = useTranslation()
  return (
    <Badge tone={STATE_TONE[state]} dot>
      {t(`candidates.states.${state}`)}
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
  tone?: 'accent' | 'warning'
  loading?: boolean
}) {
  return (
    <Card className={cx('lc-tile', tone && `lc-tile--${tone}`)}>
      <div className="lc-tile__head">
        <span className="lc-tile__label">{label}</span>
        <Icon name={icon} size={15} className="lc-tile__icon" />
      </div>
      {loading ? (
        <Skeleton width="3.5rem" height="1.5rem" />
      ) : (
        <span className="lc-tile__value">{value}</span>
      )}
    </Card>
  )
}

/* Accept ------------------------------------------------------------------ */

/**
 * The suggestion as it would be filed, open to correction first.
 *
 * Wording, type and reach are all editable because the detector guessed them from a
 * sentence; what is not editable is the review that follows, which is why the note sits
 * above the button rather than in a tooltip.
 */
function AcceptForm({
  candidate,
  pending,
  onCancel,
  onSubmit,
}: {
  candidate: Candidate
  pending: boolean
  onCancel: () => void
  onSubmit: (body: {
    content: string
    type: KnowledgeType
    scope: KnowledgeScope
    source_text: string
    explanation: string
    tags: string[]
  }) => void
}) {
  const { t } = useTranslation()
  const [content, setContent] = useState(candidate.suggested_content)
  const [type, setType] = useState<KnowledgeType>(candidate.detected_type)
  const [scope, setScope] = useState<KnowledgeScope>(candidate.proposed_scope)
  const [sourceText, setSourceText] = useState('')
  const [explanation, setExplanation] = useState('')
  const [tags, setTags] = useState('')

  const valid = content.trim().length > 0
  // The one thing that needs nobody: a personal preference touches this person's
  // wording and no fact anyone else will read.
  const immediate = type === 'preference' && scope === 'user'

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!valid) {
      toast.warning(t('candidates.accept.contentRequired'))
      return
    }
    onSubmit({
      content: content.trim(),
      type,
      scope,
      source_text: sourceText.trim(),
      explanation: explanation.trim(),
      tags: tags
        .split(',')
        .map((tag) => tag.trim())
        .filter(Boolean)
        .slice(0, MAX_TAGS),
    })
  }

  return (
    <form className="lc-form" onSubmit={submit}>
      <p className={cx('lc-form__note', immediate && 'lc-form__note--immediate')}>
        {immediate ? t('candidates.prompt.immediate') : t('candidates.accept.note')}
      </p>

      <div className="lc-form__row">
        <Field label={t('candidates.form.type')} htmlFor="accept-type">
          <Select
            id="accept-type"
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

        <Field label={t('candidates.form.scope')} htmlFor="accept-scope">
          <Select
            id="accept-scope"
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

      <Field
        label={t('candidates.form.content')}
        hint={t('candidates.form.contentHint')}
        htmlFor="accept-content"
      >
        <Textarea
          id="accept-content"
          rows={4}
          required
          autoFocus
          maxLength={MAX_CONTENT}
          value={content}
          onChange={(event) => setContent(event.target.value)}
        />
      </Field>

      <Field
        label={t('candidates.form.sourceText')}
        hint={t('candidates.form.sourceTextHint')}
        htmlFor="accept-source"
      >
        <Input
          id="accept-source"
          maxLength={MAX_NOTE}
          value={sourceText}
          onChange={(event) => setSourceText(event.target.value)}
        />
      </Field>

      <Field
        label={t('candidates.form.explanation')}
        hint={t('candidates.form.explanationHint')}
        htmlFor="accept-explanation"
      >
        <Textarea
          id="accept-explanation"
          rows={3}
          maxLength={MAX_NOTE}
          value={explanation}
          onChange={(event) => setExplanation(event.target.value)}
        />
      </Field>

      <Field
        label={t('candidates.form.tags')}
        hint={t('candidates.form.tagsHint')}
        htmlFor="accept-tags"
      >
        <Input id="accept-tags" value={tags} onChange={(event) => setTags(event.target.value)} />
      </Field>

      <div className="lc-decide">
        <Button variant="ghost" type="button" onClick={onCancel}>
          {t('common.cancel')}
        </Button>
        <Button variant="primary" type="submit" loading={pending} disabled={!valid}>
          {t('candidates.accept.submit')}
        </Button>
      </div>
    </form>
  )
}

/* Detail ------------------------------------------------------------------ */

function DetailDialog({ id, onClose }: { id: string; onClose: () => void }) {
  const { t, language } = useTranslation()
  const query = useCandidate(id)
  const accept = useAcceptCandidate()
  const dismiss = useDismissCandidate()

  const [accepting, setAccepting] = useState(false)
  const [reason, setReason] = useState('')
  const [explaining, setExplaining] = useState(false)

  const candidate = query.data

  const signalLabel = (signal: string) => {
    const key = SIGNAL_KEYS[signal]
    return key ? t(key) : signal
  }

  const close = (rejected: boolean) => {
    // The backend refuses a rejection with no reason, so it is stopped here rather than
    // sent and bounced back as a validation error.
    if (rejected && !reason.trim()) {
      toast.warning(t('candidates.reject.reasonRequired'))
      return
    }
    dismiss.mutate(
      { id, body: { reason: reason.trim(), rejected } },
      {
        onSuccess: () => {
          setReason('')
          toast.success(
            t(rejected ? 'candidates.reject.done' : 'candidates.dismiss.done'),
            t(rejected ? 'candidates.reject.note' : 'candidates.dismiss.note'),
          )
        },
        onError: (error: Error) => toast.error(t('errors.title'), error.message),
      },
    )
  }

  const onAccept = (body: Parameters<Parameters<typeof AcceptForm>[0]['onSubmit']>[0]) =>
    accept.mutate(
      { id, body },
      {
        onSuccess: () => {
          setAccepting(false)
          toast.success(t('candidates.accept.done'), t('candidates.accept.note'))
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
  } else if (!candidate) {
    body = (
      <div className="lc-detail__skeletons">
        {Array.from({ length: 5 }, (_, index) => (
          <Skeleton key={index} height="2rem" />
        ))}
      </div>
    )
  } else {
    // A resolution is final: the server refuses a second one, so the buttons go away
    // rather than offering an action that would only fail.
    const open = candidate.can_resolve && candidate.state === 'offered'
    body = (
      <div className="lc-detail">
        <div className="lc-detail__badges">
          <TypeBadge type={candidate.detected_type} />
          <Badge tone={SCOPE_TONE[candidate.proposed_scope]}>
            {t(`knowledge.scopes.${candidate.proposed_scope}`)}
          </Badge>
          <StateBadge state={candidate.state} />
          <span className="lc-signal">{signalLabel(candidate.signal)}</span>
        </div>

        <div className="lc-compare">
          <div className="lc-compare__cell">
            <h3 className="lc-section__title">{t('candidates.detail.rawText')}</h3>
            <blockquote className="lc-quote">{candidate.raw_text}</blockquote>
            <p className="lc-section__note">{t('candidates.detail.rawTextHint')}</p>
          </div>
          <div className="lc-compare__cell">
            <h3 className="lc-section__title">{t('candidates.detail.suggested')}</h3>
            <blockquote className="lc-quote lc-quote--suggested">
              {candidate.suggested_content}
            </blockquote>
          </div>
        </div>

        <dl className="lc-grid">
          <div>
            <dt>{t('candidates.detail.signal')}</dt>
            <dd>{signalLabel(candidate.signal)}</dd>
          </div>
          <div>
            <dt>{t('candidates.detail.confidence')}</dt>
            <dd className="ltr-nums">{formatPercent(candidate.confidence, language)}</dd>
          </div>
          <div>
            <dt>{t('candidates.detail.scope')}</dt>
            <dd>{t(`knowledge.scopes.${candidate.proposed_scope}`)}</dd>
          </div>
          <div>
            <dt>{t('candidates.detail.proposer')}</dt>
            <dd>{candidate.proposer_name || t('candidates.detail.unknownProposer')}</dd>
          </div>
          <div>
            <dt>{t('candidates.detail.created')}</dt>
            <dd className="ltr-nums">{formatDateTime(candidate.created_at, language)}</dd>
          </div>
          <div>
            <dt>{t('candidates.detail.resolved')}</dt>
            <dd className="ltr-nums">{formatDateTime(candidate.resolved_at, language)}</dd>
          </div>
          <div>
            <dt>{t('candidates.detail.resolutionReason')}</dt>
            <dd>{candidate.resolution_reason || t('candidates.detail.noReason')}</dd>
          </div>
        </dl>

        {candidate.corrects_item_id && (
          <section className="lc-section">
            <h3 className="lc-section__title">{t('candidates.detail.correctsItem')}</h3>
            <span className="lc-signal">{candidate.corrects_item_id}</span>
            <Link className="lc-link" to="/knowledge" onClick={onClose}>
              <Icon name="externalLink" size={13} />
              {t('candidates.detail.openKnowledge')}
            </Link>
          </section>
        )}

        {candidate.promoted_item_id && (
          <section className="lc-section">
            <h3 className="lc-section__title">{t('candidates.detail.promotedItem')}</h3>
            <span className="lc-signal">{candidate.promoted_item_id}</span>
            <Link className="lc-link" to="/knowledge" onClick={onClose}>
              <Icon name="externalLink" size={13} />
              {t('candidates.detail.openKnowledge')}
            </Link>
          </section>
        )}

        {candidate.answer_id && (
          <section className="lc-section">
            <h3 className="lc-section__title">{t('candidates.detail.sourceAnswer')}</h3>
            <span className="lc-signal">{candidate.answer_id}</span>
            <Button size="sm" variant="secondary" icon="info" onClick={() => setExplaining(true)}>
              {t('candidates.detail.openAnswer')}
            </Button>
          </section>
        )}

        {open ? (
          accepting ? (
            <section className="lc-section">
              <h3 className="lc-section__title">{t('candidates.accept.title')}</h3>
              <AcceptForm
                candidate={candidate}
                pending={accept.isPending}
                onCancel={() => setAccepting(false)}
                onSubmit={onAccept}
              />
            </section>
          ) : (
            <section className="lc-section">
              <h3 className="lc-section__title">{t('candidates.accept.action')}</h3>
              <p className="lc-form__note">{t('candidates.accept.note')}</p>
              <Field label={t('candidates.reject.reason')} htmlFor="candidate-reason">
                <Textarea
                  id="candidate-reason"
                  rows={2}
                  maxLength={MAX_REASON}
                  placeholder={t('candidates.dismiss.reasonPlaceholder')}
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                />
              </Field>
              <div className="lc-decide">
                <Button
                  size="sm"
                  variant="primary"
                  icon="check"
                  disabled={dismiss.isPending}
                  onClick={() => setAccepting(true)}
                >
                  {t('candidates.accept.action')}
                </Button>
                <Button
                  size="sm"
                  variant="secondary"
                  icon="archive"
                  loading={dismiss.isPending && dismiss.variables?.body.rejected === false}
                  disabled={dismiss.isPending}
                  onClick={() => close(false)}
                >
                  {t('candidates.dismiss.action')}
                </Button>
                <Button
                  size="sm"
                  variant="danger"
                  icon="close"
                  loading={dismiss.isPending && dismiss.variables?.body.rejected === true}
                  disabled={dismiss.isPending}
                  onClick={() => close(true)}
                >
                  {t('candidates.reject.action')}
                </Button>
              </div>
              <p className="lc-section__note">{t('candidates.reject.note')}</p>
            </section>
          )
        ) : (
          <p className="lc-section__note">{t('candidates.detail.closed')}</p>
        )}

        {explaining && candidate.answer_id && (
          <WhyThisAnswer answerId={candidate.answer_id} onClose={() => setExplaining(false)} />
        )}
      </div>
    )
  }

  return (
    <Modal open wide title={t('candidates.detail.title')} onClose={onClose}>
      {body}
    </Modal>
  )
}

/* Page -------------------------------------------------------------------- */

export function CandidatesPage() {
  const { t, language } = useTranslation()
  const { data: user } = useCurrentUser()
  const isMobile = useIsMobile()

  const [tab, setTab] = useState<CandidateState>('offered')
  const [offset, setOffset] = useState(0)
  const [opened, setOpened] = useState<string | null>(null)
  const [answerId, setAnswerId] = useState<string | null>(null)

  const allowed = can(user, 'knowledge.propose')

  // A change of tab can leave the current page past the end of the new result set.
  useEffect(() => setOffset(0), [tab])

  const query = useMemo<CandidateQuery>(
    () => ({ state: [tab], mine: true, limit: PAGE_SIZE, offset }),
    [tab, offset],
  )

  const list = useCandidateList(query, allowed)
  const stats = useCandidateStats(allowed)

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
  const byState = stats.data?.by_state ?? {}

  const tabCount = (id: CandidateState): number | undefined => {
    if (!stats.data) return undefined
    return id === 'offered' ? stats.data.open_for_me : (byState[id] ?? 0)
  }

  const signalLabel = (signal: string) => {
    const key = SIGNAL_KEYS[signal]
    return key ? t(key) : signal
  }

  const answerLink = (candidate: Candidate) =>
    candidate.answer_id ? (
      <Button
        size="sm"
        variant="ghost"
        icon="info"
        onClick={(event) => {
          event.stopPropagation()
          setAnswerId(candidate.answer_id)
        }}
      >
        {t('why.action')}
      </Button>
    ) : null

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
      <div className="lc-table__skeletons">
        {Array.from({ length: 6 }, (_, index) => (
          <Skeleton key={index} height="2.25rem" />
        ))}
      </div>
    )
  } else if (items.length === 0) {
    body = (
      <EmptyState
        icon="inbox"
        title={t('candidates.empty.title')}
        body={t('candidates.empty.body')}
      />
    )
  } else if (isMobile) {
    body = (
      <div className="lc-cards">
        {items.map((candidate) => (
          <Card
            key={candidate.id}
            className="lc-card"
            role="button"
            tabIndex={0}
            onClick={() => setOpened(candidate.id)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault()
                setOpened(candidate.id)
              }
            }}
          >
            <div className="lc-card__badges">
              <TypeBadge type={candidate.detected_type} />
              <StateBadge state={candidate.state} />
            </div>
            <p className="lc-card__content">{candidate.suggested_content}</p>
            <div className="lc-card__grid">
              <div>
                <span className="lc-card__label">{t('candidates.table.signal')}</span>
                <span className="lc-card__value">{signalLabel(candidate.signal)}</span>
              </div>
              <div>
                <span className="lc-card__label">{t('candidates.table.confidence')}</span>
                <span className="lc-card__value ltr-nums">
                  {formatPercent(candidate.confidence, language)}
                </span>
              </div>
              <div>
                <span className="lc-card__label">{t('candidates.table.proposer')}</span>
                <span className="lc-card__value">
                  {candidate.proposer_name || t('candidates.detail.unknownProposer')}
                </span>
              </div>
              <div>
                <span className="lc-card__label">{t('candidates.table.created')}</span>
                <span className="lc-card__value ltr-nums">
                  {formatDate(candidate.created_at, language)}
                </span>
              </div>
            </div>
            {answerLink(candidate)}
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
              <th>{t('candidates.table.content')}</th>
              <th>{t('candidates.table.type')}</th>
              <th>{t('candidates.table.signal')}</th>
              <th>{t('candidates.table.confidence')}</th>
              <th>{t('candidates.table.proposer')}</th>
              <th>{t('candidates.table.created')}</th>
              <th>{t('candidates.table.state')}</th>
              <th>{t('candidates.table.answer')}</th>
            </tr>
          </thead>
          <tbody>
            {items.map((candidate) => (
              <tr
                key={candidate.id}
                className="lc-table__row"
                onClick={() => setOpened(candidate.id)}
              >
                <td>
                  <span className="lc-table__content">{candidate.suggested_content}</span>
                </td>
                <td>
                  <TypeBadge type={candidate.detected_type} />
                </td>
                <td className="lc-table__muted">{signalLabel(candidate.signal)}</td>
                <td className="table__numeric">
                  {formatPercent(candidate.confidence, language)}
                </td>
                <td className="lc-table__muted">
                  {candidate.proposer_name || t('candidates.detail.unknownProposer')}
                </td>
                <td
                  className="table__numeric"
                  title={formatDateTime(candidate.created_at, language)}
                >
                  {formatDate(candidate.created_at, language)}
                </td>
                <td>
                  <StateBadge state={candidate.state} />
                </td>
                <td>
                  <div className="lc-table__actions">{answerLink(candidate)}</div>
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
          <h1 className="page__title">{t('candidates.title')}</h1>
          <p className="page__subtitle">{t('candidates.subtitle')}</p>
        </div>
      </header>

      <div className="lc-stats">
        <StatTile
          label={t('candidates.stats.total')}
          icon="database"
          loading={stats.isLoading}
          value={formatNumber(stats.data?.total ?? 0, language)}
        />
        <StatTile
          label={t('candidates.stats.openForMe')}
          icon="clock"
          tone={(stats.data?.open_for_me ?? 0) > 0 ? 'warning' : undefined}
          loading={stats.isLoading}
          value={formatNumber(stats.data?.open_for_me ?? 0, language)}
        />
        {CANDIDATE_STATES.filter((state) => state !== 'offered').map((state) => (
          <StatTile
            key={state}
            label={t(`candidates.stats.${state}`)}
            icon={STATE_ICON[state]}
            loading={stats.isLoading}
            value={formatNumber(byState[state] ?? 0, language)}
          />
        ))}
      </div>

      <Tabs
        items={CANDIDATE_STATES.map((id) => ({
          id,
          label: t(`candidates.tabs.${id}`),
          count: tabCount(id),
        }))}
        active={tab}
        onChange={setTab}
      />

      {isMobile && !list.isError && items.length > 0 ? body : <Card className="lc-table">{body}</Card>}

      {showCount && (
        <div className="lc-pager">
          <span className="lc-pager__count">
            {t('common.rowsShown', { shown: offset + items.length, total })}
          </span>
          {total > PAGE_SIZE && (
            <div className="lc-pager__buttons">
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

      {opened && <DetailDialog key={opened} id={opened} onClose={() => setOpened(null)} />}

      {answerId && <WhyThisAnswer answerId={answerId} onClose={() => setAnswerId(null)} />}
    </div>
  )
}
