import { useState } from 'react'
import { Badge } from '@/components/ui/primitives'
import { cx } from '@/utils/cx'
import { Icon } from '@/components/ui/Icon'
import { useTranslation } from '@/hooks/useTranslation'
import { formatDuration, formatNumber, formatPercent } from '@/utils/format'
import type { ChatResponse } from '@/types/api'

/**
 * What the pipeline did, folded away by default.
 *
 * The backend already reports its own plan, coverage and validation findings; showing
 * them is what separates an answer you can audit from one you have to take on trust.
 */
export function AnswerDetails({ response }: { response: ChatResponse }) {
  const { t, language } = useTranslation()
  const [open, setOpen] = useState(false)

  const { plan, coverage, validation, timings_ms: timings } = response
  const total = timings.total_ms ?? 0
  const complete = validation?.complete ?? true

  return (
    <div className="answer-details">
      <button
        type="button"
        className="answer-details__toggle"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <Icon name="chevronDown" size={14} className={cx('answer-details__caret', open && 'is-open')} />
        {t('chat.details')}
        <span className="answer-details__summary">
          <Badge tone={complete ? 'success' : 'warning'}>
            <span title={t('answer.completeHint')}>
              {complete ? t('answer.complete') : t('answer.incomplete')}
            </span>
          </Badge>
          {/*
            The percentage beside the badge measures something else entirely: how much of
            the retrieved evidence reached the answer. Shown with its own label because
            "100%" next to "incomplete" reads as a contradiction rather than as two
            separate readings, and a reader who decides the panel is broken stops using it.
          */}
          {coverage && (
            <span className="ltr-nums" title={t('answer.completenessHint')}>
              {t('answer.completeness')}: {formatPercent(coverage.completeness_score, language)}
            </span>
          )}
          {total > 0 && <span className="ltr-nums">{formatDuration(total, language)}</span>}
        </span>
      </button>

      {open && (
        <div className="answer-details__body">
          <dl className="detail-grid">
            <Row label={t('answer.model')} value={response.model} mono />
            {plan?.intent && <Row label={t('answer.intent')} value={plan.intent} />}
            <Row label={t('answer.retrieved')} value={formatNumber(response.retrieved_chunks, language)} mono />
            {coverage && (
              <>
                <Row label={t('answer.passes')} value={formatNumber(coverage.passes, language)} mono />
                <Row
                  label={t('answer.completeness')}
                  value={formatPercent(coverage.completeness_score, language)}
                  mono
                />
                <Row
                  label={t('answer.complete')}
                  value={complete ? '✔' : '✖'}
                />
              </>
            )}
          </dl>

          {plan && (plan.parts?.length ?? 0) > 1 && (
            <Section title={t('answer.parts')}>
              <ol className="detail-list">
                {plan.parts!.map((part) => (
                  <li key={part}>{part}</li>
                ))}
              </ol>
            </Section>
          )}

          {plan && (plan.rewrites?.length ?? 0) > 0 && (
            <Section title={t('answer.rewrites')}>
              <ul className="detail-list">
                {plan.rewrites!.map((note) => (
                  <li key={note} dir="rtl">{note}</li>
                ))}
              </ul>
            </Section>
          )}

          {plan && plan.requirements.length > 0 && (
            <Section title={t('answer.requirements')}>
              <ul className="detail-list">
                {plan.requirements.map((requirement) => (
                  <li key={requirement}>{requirement}</li>
                ))}
              </ul>
            </Section>
          )}

          {coverage && coverage.missing_entities.length > 0 && (
            <Section title={t('answer.missing')} tone="warning">
              <ul className="detail-list">
                {coverage.missing_entities.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </Section>
          )}

          {validation && validation.conflicts.length > 0 && (
            <Section title={t('answer.conflicts')} tone="warning">
              <ul className="detail-list">
                {validation.conflicts.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </Section>
          )}

          {validation && validation.warnings.length > 0 && (
            <Section title={t('answer.warnings')}>
              <ul className="detail-list detail-list--muted">
                {validation.warnings.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </Section>
          )}

          {Object.keys(timings).length > 0 && (
            <Section title={t('answer.timings')}>
              <dl className="detail-grid">
                {Object.entries(timings)
                  .filter(([key]) => key !== 'total_ms')
                  .map(([key, value]) => (
                    <Row
                      key={key}
                      label={key.replace(/_ms$/, '').replace(/_/g, ' ')}
                      value={formatDuration(value, language)}
                      mono
                    />
                  ))}
              </dl>
            </Section>
          )}
        </div>
      )}
    </div>
  )
}

function Row({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="detail-grid__row">
      <dt>{label}</dt>
      <dd className={cx(mono && 'ltr-nums')}>{value}</dd>
    </div>
  )
}

function Section({
  title,
  tone,
  children,
}: {
  title: string
  tone?: 'warning'
  children: React.ReactNode
}) {
  return (
    <section className={cx('detail-section', tone && `detail-section--${tone}`)}>
      <h4 className="detail-section__title">{title}</h4>
      {children}
    </section>
  )
}
