import { Icon } from '@/components/ui/Icon'
import { Spinner } from '@/components/ui/primitives'
import { useTranslation } from '@/hooks/useTranslation'
import type { ChatStage } from '@/types/api'

/**
 * What the pipeline is doing, right now.
 *
 * Each line appears because the backend reported reaching that stage. Nothing is
 * scheduled or guessed: if the server says nothing, this shows only the spinner, which
 * is the truthful state of a request that has not reported in yet.
 */
export function StageIndicator({ stages }: { stages: ChatStage[] }) {
  const { t } = useTranslation()
  const current = stages[stages.length - 1]
  const finished = stages.slice(0, -1)

  return (
    <div className="msg msg--assistant">
      <span className="msg__avatar" aria-hidden="true">
        <Icon name="sparkles" size={15} />
      </span>
      <div className="msg__column">
        <div className="stage-track" role="status" aria-live="polite">
          {finished.map((stage) => (
            <div key={stage} className="stage-track__row stage-track__row--done">
              <Icon name="check" size={13} />
              <span>{t(`stages.${stage}`)}</span>
            </div>
          ))}
          <div className="stage-track__row stage-track__row--active">
            <Spinner size={13} />
            <span>{current ? t(`stages.${current}`) : t('common.loading')}</span>
          </div>
        </div>
      </div>
    </div>
  )
}
