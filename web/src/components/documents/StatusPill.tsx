import { PIPELINE_STAGES, TERMINAL_FAILURES, type DocumentStatus } from '@/types/api'
import { Badge, type BadgeTone } from '@/components/ui/primitives'
import { cx } from '@/utils/cx'
import { Icon } from '@/components/ui/Icon'
import { useTranslation } from '@/hooks/useTranslation'
import './documents.css'

function toneFor(status: DocumentStatus): BadgeTone {
  if (status === 'completed') return 'success'
  if (status === 'ocr_required') return 'warning'
  if (TERMINAL_FAILURES.has(status)) return 'danger'
  return 'info'
}

export function StatusPill({ status }: { status: DocumentStatus }) {
  const { t } = useTranslation()
  const tone = toneFor(status)
  return (
    <Badge tone={tone} dot={tone === 'info'}>
      {t(`status.${status}`)}
    </Badge>
  )
}

type StepState = 'done' | 'current' | 'pending'

function stepState(index: number, current: number, status: DocumentStatus): StepState {
  if (current < 0) return 'pending'
  if (status === 'completed' || index < current) return 'done'
  return index === current ? 'current' : 'pending'
}

export function PipelineSteps({
  status,
  errorMessage,
}: {
  status: DocumentStatus
  errorMessage?: string | null
}) {
  const { t } = useTranslation()
  const failed = TERMINAL_FAILURES.has(status)
  // The position comes from the status the server last reported, never from a timer here:
  // a backend that stalls at `embedding` shows a stalled step instead of a bar that keeps
  // creeping towards a completion that is not happening.
  const current = failed ? -1 : PIPELINE_STAGES.indexOf(status)

  return (
    <div className={cx('pipeline', failed && 'pipeline--failed')}>
      <ol className="pipeline__list">
        {PIPELINE_STAGES.map((stage, index) => {
          const state = stepState(index, current, status)
          return (
            <li
              key={stage}
              className={cx('pipeline__step', `pipeline__step--${state}`)}
              aria-current={state === 'current' ? 'step' : undefined}
            >
              <span className="pipeline__mark">{state === 'done' && <Icon name="check" size={10} />}</span>
              <span className="pipeline__label">{t(`status.${stage}`)}</span>
            </li>
          )
        })}
      </ol>

      {failed && (
        <p className="pipeline__error" role="alert">
          <Icon name="alert" size={14} />
          <span>{errorMessage || t(`status.${status}`)}</span>
        </p>
      )}
    </div>
  )
}
