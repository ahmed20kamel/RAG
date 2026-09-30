import { Link } from 'react-router-dom'
import { Icon } from '@/components/ui/Icon'
import { Modal } from '@/components/ui/Modal'
import { Badge, Button, EmptyState, ErrorState, Skeleton } from '@/components/ui/primitives'
import { cx } from '@/utils/cx'
import { useTranslation } from '@/hooks/useTranslation'
import { useWhyThisAnswer } from '@/hooks/useCandidates'
import { formatDateTime, formatNumber } from '@/utils/format'
import type {
  AnswerConflict,
  ConflictSide,
  KnowledgeContribution,
} from '@/types/candidates'
import '@/pages/candidates.css'

/**
 * What one answer was built from.
 *
 * Four separate lists, never merged: a passage from a filed document, a claim a
 * colleague taught and a reviewer approved, a rule that shaped the wording, and a
 * disagreement between two of them are four different kinds of thing, and the reader
 * judges the answer by telling them apart.
 *
 * Nothing here narrates how the model thought. The backend stores no reasoning, and
 * this renders only what it stores — an explanation made of sources can be checked
 * against those sources, while a rationale can only be believed.
 */
export function WhyThisAnswer({ answerId, onClose }: { answerId: string; onClose: () => void }) {
  const { t, language } = useTranslation()
  const query = useWhyThisAnswer(answerId)

  const score = (value: number) => formatNumber(Math.round(value * 1000) / 1000, language)

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
  } else if (query.isLoading) {
    body = (
      <div className="lc-detail__skeletons">
        {Array.from({ length: 6 }, (_, index) => (
          <Skeleton key={index} height="2.5rem" />
        ))}
      </div>
    )
  } else if (!query.data) {
    body = <EmptyState icon="inbox" title={t('why.empty')} />
  } else {
    const explanation = query.data

    const contribution = (item: KnowledgeContribution, policy: boolean) => (
      <li key={`${item.item_id}-${item.version_id}`}>
        <div className={cx('lc-evidence', policy && 'lc-evidence--policy')}>
          <div className="lc-evidence__head">
            <Badge tone={policy ? 'warning' : 'info'}>{item.type || '—'}</Badge>
            <Badge tone="neutral">{item.scope || '—'}</Badge>
            {item.conflicted && <Badge tone="danger">{t('why.conflicts.title')}</Badge>}
          </div>
          <p className="lc-evidence__body">{item.content}</p>
          {item.source_text && <blockquote className="lc-quote">{item.source_text}</blockquote>}
          <div className="lc-evidence__meta">
            <span className="ltr-nums">
              {t('why.knowledge.version')} {formatNumber(item.version_no, language)}
            </span>
            <span>
              {t('why.knowledge.influence')}: {item.influence || '—'}
            </span>
            <span className="ltr-nums">
              {t('why.knowledge.score')} {score(item.score)}
            </span>
          </div>
          {item.source_document_id && (
            <Link
              className="lc-link"
              to={`/documents/${item.source_document_id}`}
              onClick={onClose}
            >
              <Icon name="externalLink" size={13} />
              {t('why.evidence.open')}
            </Link>
          )}
        </div>
      </li>
    )

    const side = (value: ConflictSide) => (
      <div className="lc-side">
        <span className="lc-side__label">
          {value.kind === 'document'
            ? t('why.conflicts.kinds.document')
            : value.kind === 'knowledge'
              ? t('why.conflicts.kinds.knowledge')
              : value.kind}
          {value.label ? ` — ${value.label}` : ''}
        </span>
        <span className="lc-side__values">{value.values.join('، ') || '—'}</span>
        <span className="lc-side__label lc-signal">{value.ref}</span>
      </div>
    )

    const conflict = (item: AnswerConflict, index: number) => (
      <li key={`${item.left.ref}-${item.right.ref}-${index}`}>
        <div
          className={cx(
            'lc-evidence',
            'lc-evidence--conflict',
            !item.resolved && 'lc-evidence--unresolved',
          )}
        >
          <div className="lc-evidence__head">
            <Badge tone={item.resolved ? 'success' : 'danger'} dot>
              {item.resolved ? t('why.conflicts.resolved') : t('why.conflicts.unresolved')}
            </Badge>
          </div>
          <p className="lc-evidence__body">{item.description}</p>
          <div className="lc-sides">
            {side(item.left)}
            {side(item.right)}
          </div>
          <div className="lc-evidence__meta">
            <span>
              {t('why.conflicts.basis')}: {item.basis || '—'}
            </span>
            <span className="lc-signal">
              {t('why.conflicts.winner')}: {item.winner || '—'}
            </span>
            {item.shared_terms.length > 0 && (
              <span>
                {t('why.conflicts.sharedTerms')}: {item.shared_terms.join('، ')}
              </span>
            )}
          </div>
        </div>
      </li>
    )

    body = (
      <div className="lc-why">
        <p className="lc-why__lead">{t('why.lead')}</p>

        {explanation.unresolved_conflicts > 0 && (
          <p className="lc-why__alert" role="alert">
            <Icon name="alert" size={16} />
            <span className="ltr-nums">
              {t('why.unresolved', {
                count: formatNumber(explanation.unresolved_conflicts, language),
              })}
            </span>
          </p>
        )}

        <dl className="lc-grid">
          <div>
            <dt>{t('why.question')}</dt>
            <dd>{explanation.question || '—'}</dd>
          </div>
          <div>
            <dt>{t('why.model')}</dt>
            <dd className="ltr-nums">{explanation.model || '—'}</dd>
          </div>
          <div>
            <dt>{t('why.createdAt')}</dt>
            <dd className="ltr-nums">{formatDateTime(explanation.created_at, language)}</dd>
          </div>
        </dl>

        {!explanation.knowledge_layer_enabled && (
          <p className="lc-section__note">{t('why.layerDisabled')}</p>
        )}

        <section className="lc-section">
          <h3 className="lc-section__title">{t('why.evidence.title')}</h3>
          <p className="lc-section__note">{t('why.evidence.note')}</p>
          {explanation.document_evidence.length > 0 ? (
            <ul className="lc-why__list">
              {explanation.document_evidence.map((item) => (
                <li key={`${item.document_id}-${item.citation}-${item.section_id}`}>
                  <div className="lc-evidence">
                    <div className="lc-evidence__head">
                      <span className="lc-evidence__cite ltr-nums">{item.citation}</span>
                      <span className="lc-evidence__title">{item.filename}</span>
                    </div>
                    <div className="lc-evidence__meta">
                      <span>
                        {t('why.evidence.section')}: {item.section || '—'}
                      </span>
                      <span className="ltr-nums">
                        {t('why.evidence.score')} {score(item.score)}
                      </span>
                    </div>
                    <Link
                      className="lc-link"
                      to={`/documents/${item.document_id}`}
                      onClick={onClose}
                    >
                      <Icon name="externalLink" size={13} />
                      {t('why.evidence.open')}
                    </Link>
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p className="lc-section__empty">{t('why.evidence.empty')}</p>
          )}
        </section>

        <section className="lc-section">
          <h3 className="lc-section__title">{t('why.knowledge.title')}</h3>
          <p className="lc-section__note">{t('why.knowledge.note')}</p>
          {explanation.knowledge_used.length > 0 ? (
            <ul className="lc-why__list">
              {explanation.knowledge_used.map((item) => contribution(item, false))}
            </ul>
          ) : (
            <p className="lc-section__empty">{t('why.knowledge.empty')}</p>
          )}
        </section>

        <section className="lc-section">
          <h3 className="lc-section__title">{t('why.policies.title')}</h3>
          <p className="lc-section__note">{t('why.policies.note')}</p>
          {explanation.policies_applied.length > 0 ? (
            <ul className="lc-why__list">
              {explanation.policies_applied.map((item) => contribution(item, true))}
            </ul>
          ) : (
            <p className="lc-section__empty">{t('why.policies.empty')}</p>
          )}
        </section>

        <section className="lc-section">
          <h3 className="lc-section__title">{t('why.conflicts.title')}</h3>
          {explanation.conflicts.length > 0 ? (
            <ul className="lc-why__list">{explanation.conflicts.map(conflict)}</ul>
          ) : (
            <p className="lc-section__empty">{t('why.conflicts.empty')}</p>
          )}
        </section>
      </div>
    )
  }

  return (
    <Modal open wide title={t('why.title')} onClose={onClose}>
      {body}
    </Modal>
  )
}
