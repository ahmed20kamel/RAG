import { useEffect, useState } from 'react'
import { Icon } from '@/components/ui/Icon'
import { Spinner } from '@/components/ui/primitives'
import { useTranslation } from '@/hooks/useTranslation'
import type { ChatLoad, ChatStage } from '@/types/api'
import { usePreferences } from '@/state/preferences'

/** Past this, a wait with no one ahead is said to be longer than usual. */
const SLOW_FACTOR = 1.5

/**
 * What the pipeline is doing, right now.
 *
 * Each line appears because the backend reported reaching that stage. Nothing is
 * scheduled or guessed: if the server says nothing, this shows only the spinner, which
 * is the truthful state of a request that has not reported in yet.
 *
 * At a busy moment the server also says how many questions are ahead of this one and
 * roughly how long that is, so a queue reads as a queue rather than as a page that hung.
 */
export function StageIndicator({
  stages,
  load = null,
  startedAt = null,
}: {
  stages: ChatStage[]
  load?: ChatLoad | null
  startedAt?: number | null
}) {
  const { t } = useTranslation()
  const current = stages[stages.length - 1]
  const finished = stages.slice(0, -1)
  const now = useNow(startedAt !== null)
  const deep = usePreferences((state) => state.deepThinking)

  const elapsed = startedAt === null ? 0 : Math.max(0, Math.round((now - startedAt) / 1000))
  const remaining = load ? Math.max(0, load.waitSeconds - Math.round((now - load.at) / 1000)) : 0
  const queued = load !== null && load.ahead > 0
  const slow = !queued && load !== null && elapsed > load.waitSeconds * SLOW_FACTOR

  const duration = (seconds: number) =>
    seconds < 60
      ? t('load.seconds', { n: seconds })
      : t('load.minutes', { n: Math.round(seconds / 60) })

  return (
    <div className="msg msg--assistant">
      <span className="msg__avatar" aria-hidden="true">
        <Icon name="sparkles" size={15} />
      </span>
      <div className="msg__column">
        {deep && !queued && (
          <p className="deep-notice">
            <Icon name="sparkles" size={13} />
            {t('chat.deepWorking')}
          </p>
        )}
        {(queued || (slow && !deep)) && (
          <div className="load-notice" role="status" aria-live="polite">
            <Icon name="clock" size={15} />
            <div>
              {queued ? (
                <>
                  <p>{t('load.queued', { count: load.ahead })}</p>
                  <p>
                    {t('load.wait', {
                      time: remaining < 60 ? t('load.lessThanMinute') : duration(remaining),
                    })}
                  </p>
                </>
              ) : (
                <p>{t('load.slow')}</p>
              )}
              <p className="load-notice__hint">{t('load.keepOpen')}</p>
            </div>
          </div>
        )}
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
            {elapsed >= 10 && (
              <span className="stage-track__elapsed">{t('load.elapsed', { time: duration(elapsed) })}</span>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

/** The current time, refreshed every second while `running`. */
function useNow(running: boolean): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!running) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [running])
  return now
}
