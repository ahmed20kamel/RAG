import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Badge,
  Card,
  CardHeader,
  EmptyState,
  ErrorState,
  Segmented,
  Skeleton,
  type BadgeTone,
} from '@/components/ui/primitives'
import { can, useCurrentUser } from '@/hooks/useAuth'
import { useTranslation } from '@/hooks/useTranslation'
import { monitoringApi, type MonitoringResponse, type Spread } from '@/services/monitoring'
import { formatBytes, formatDateTime, formatDuration, formatNumber, formatPercent } from '@/utils/format'
import './monitoring.css'

type Window = '1' | '7' | '30'

/** Outcomes in the order a reader weighs them, with the tone each deserves. */
const OUTCOMES: { key: string; tone: BadgeTone }[] = [
  { key: 'answered', tone: 'success' },
  { key: 'clarified', tone: 'info' },
  { key: 'refused', tone: 'warning' },
  { key: 'web', tone: 'neutral' },
  { key: 'error', tone: 'danger' },
]

function Reachable({ ok, label }: { ok: boolean | undefined; label: string }) {
  const { t } = useTranslation()
  return (
    <div className="monitor-component">
      <span className="monitor-component__name">{label}</span>
      <Badge tone={ok ? 'success' : 'danger'} dot>
        {ok ? t('monitor.up') : t('monitor.down')}
      </Badge>
    </div>
  )
}

function SpreadRow({ label, spread }: { label: string; spread: Spread }) {
  const { language } = useTranslation()
  return (
    <tr>
      <th scope="row">{label}</th>
      <td>{formatDuration(spread.p50, language)}</td>
      <td>{formatDuration(spread.p95, language)}</td>
      <td>{formatDuration(spread.max, language)}</td>
    </tr>
  )
}

function Content({ data }: { data: MonitoringResponse }) {
  const { t, language } = useTranslation()
  const { components, traffic } = data
  const backup = components.backup
  const peak = Math.max(1, ...traffic.daily.map((d) => d.total))

  return (
    <>
      {traffic.alerts.length > 0 && (
        <section className="monitor-alerts" aria-label={t('monitor.alerts')}>
          {traffic.alerts.map((alert) => (
            <div key={alert.key} className={`monitor-alert monitor-alert--${alert.level}`} role="status">
              <Badge tone={alert.level === 'critical' ? 'danger' : 'warning'}>
                {alert.level === 'critical' ? t('monitor.critical') : t('monitor.warning')}
              </Badge>
              <span>{alert.message}</span>
            </div>
          ))}
        </section>
      )}

      <div className="monitor-grid">
        <Card padded>
          <CardHeader title={t('monitor.components')} />
          <Reachable ok={components.generation.reachable} label={`${t('monitor.generation')} · ${data.model}`} />
          <Reachable ok={components.embeddings.reachable} label={t('monitor.embeddings')} />
          <Reachable ok={components.vectors.reachable} label={t('monitor.vectors')} />
          <div className="monitor-component">
            <span className="monitor-component__name">{t('monitor.reranker')}</span>
            <Badge tone={components.reranker.error ? 'warning' : 'neutral'}>
              {components.reranker.name === 'cross'
                ? components.reranker.error ? t('monitor.rerankerFallback') : t('monitor.rerankerCross')
                : t('monitor.rerankerFeature')}
            </Badge>
          </div>
        </Card>

        <Card padded>
          <CardHeader title={t('monitor.backup')} />
          <dl className="monitor-facts">
            <dt>{t('monitor.lastBackup')}</dt>
            <dd>
              {backup.last_success ? formatDateTime(backup.last_success, language) : t('monitor.never')}
              {backup.age_hours !== null && (
                <Badge tone={backup.stale ? 'danger' : 'success'}>
                  {t('monitor.hoursAgo', { hours: formatNumber(backup.age_hours, language) })}
                </Badge>
              )}
            </dd>
            <dt>{t('monitor.backupSize')}</dt>
            <dd>{backup.last_size ? formatBytes(backup.last_size, language) : '—'}</dd>
            <dt>{t('monitor.secondCopy')}</dt>
            <dd>
              <Badge tone={backup.mirror_configured ? 'success' : 'warning'}>
                {backup.mirror_configured ? t('monitor.yes') : t('monitor.notConfigured')}
              </Badge>
            </dd>
            <dt>{t('monitor.disk')}</dt>
            <dd>
              {t('monitor.diskFree', {
                free: formatNumber(components.disk.free_gb, language),
                total: formatNumber(components.disk.total_gb, language),
              })}
            </dd>
          </dl>
        </Card>

        <Card padded>
          <CardHeader title={t('monitor.traffic', { days: traffic.window_days })} />
          <p className="monitor-total">
            {formatNumber(traffic.total, language)} <span>{t('monitor.requests')}</span>
          </p>
          <div className="monitor-outcomes">
            {OUTCOMES.filter((o) => traffic.by_outcome[o.key]).map((o) => (
              <Badge key={o.key} tone={o.tone}>
                {t(`monitor.outcome.${o.key}` as 'monitor.outcome.answered')} ·{' '}
                {formatNumber(traffic.by_outcome[o.key], language)} (
                {formatPercent(traffic.rates[o.key] ?? 0, language)})
              </Badge>
            ))}
          </div>
          <dl className="monitor-facts">
            <dt>{t('monitor.completeRate')}</dt>
            <dd>{formatPercent(traffic.quality.complete_rate, language)}</dd>
            <dt>{t('monitor.unsupported')}</dt>
            <dd>{formatNumber(traffic.quality.with_unsupported_values, language)}</dd>
            <dt>{t('monitor.withConflicts')}</dt>
            <dd>{formatNumber(traffic.quality.with_conflicts, language)}</dd>
          </dl>
        </Card>
      </div>

      <div className="monitor-grid monitor-grid--wide">
        <Card padded>
          <CardHeader title={t('monitor.latency')} />
          <table className="monitor-table">
            <thead>
              <tr>
                <th scope="col">{t('monitor.stage')}</th>
                <th scope="col">{t('monitor.median')}</th>
                <th scope="col">p95</th>
                <th scope="col">{t('monitor.max')}</th>
              </tr>
            </thead>
            <tbody>
              <SpreadRow label={t('monitor.total')} spread={traffic.latency.total} />
              <SpreadRow label={t('monitor.retrieval')} spread={traffic.latency.retrieval} />
              <SpreadRow label={t('monitor.generationTime')} spread={traffic.latency.generation} />
            </tbody>
          </table>
        </Card>

        <Card padded>
          <CardHeader title={t('monitor.daily')} />
          {traffic.daily.length === 0 ? (
            <p className="monitor-muted">{t('monitor.noTraffic')}</p>
          ) : (
            <ul className="monitor-daily">
              {traffic.daily.map((day) => (
                <li key={day.date}>
                  <span className="monitor-daily__date">{day.date}</span>
                  <span className="monitor-daily__bar" aria-hidden>
                    <span
                      className="monitor-daily__fill monitor-daily__fill--answered"
                      style={{ inlineSize: `${(day.answered / peak) * 100}%` }}
                    />
                    <span
                      className="monitor-daily__fill monitor-daily__fill--refused"
                      style={{ inlineSize: `${((day.refused + day.clarified) / peak) * 100}%` }}
                    />
                    <span
                      className="monitor-daily__fill monitor-daily__fill--error"
                      style={{ inlineSize: `${(day.error / peak) * 100}%` }}
                    />
                  </span>
                  <span className="monitor-daily__count">
                    {formatNumber(day.total, language)} · p95 {formatDuration(day.p95_ms, language)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <div className="monitor-grid monitor-grid--wide">
        <Card padded>
          <CardHeader title={t('monitor.refusals')} />
          {traffic.refusal_reasons.length === 0 ? (
            <p className="monitor-muted">{t('monitor.noRefusals')}</p>
          ) : (
            <ul className="monitor-list">
              {traffic.refusal_reasons.map((r) => (
                <li key={r.reason}>
                  <span>{r.label}</span>
                  <Badge>{formatNumber(r.count, language)}</Badge>
                </li>
              ))}
            </ul>
          )}
          <p className="monitor-muted">{t('monitor.refusalHint')}</p>
        </Card>

        <Card padded>
          <CardHeader title={t('monitor.slowest')} />
          {traffic.slowest.length === 0 ? (
            <p className="monitor-muted">{t('monitor.noTraffic')}</p>
          ) : (
            <ul className="monitor-list">
              {traffic.slowest.map((s) => (
                <li key={`${s.answer_id}-${s.created_at}`}>
                  <span>{formatDateTime(s.created_at, language)}</span>
                  <Badge tone="neutral">{formatDuration(s.total_ms, language)}</Badge>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </>
  )
}

export function MonitoringPage() {
  const { t } = useTranslation()
  const { data: user } = useCurrentUser()
  const [days, setDays] = useState<Window>('7')
  const allowed = can(user, 'system.monitor')

  const query = useQuery({
    queryKey: ['monitoring', days],
    queryFn: () => monitoringApi.get(Number(days)),
    enabled: allowed,
    refetchInterval: 60_000,
  })

  if (!allowed) {
    return (
      <div className="page">
        <EmptyState icon="shield" title={t('monitor.title')} body={t('monitor.adminOnly')} />
      </div>
    )
  }

  return (
    <div className="page">
      <header className="page__head">
        <h1 className="page__title">{t('monitor.title')}</h1>
        <Segmented<Window>
          label={t('monitor.window')}
          value={days}
          onChange={setDays}
          options={[
            { value: '1', label: t('monitor.day') },
            { value: '7', label: t('monitor.week') },
            { value: '30', label: t('monitor.month') },
          ]}
        />
      </header>
      {query.isLoading && (
        <div className="monitor-grid">
          <Skeleton height="9rem" />
          <Skeleton height="9rem" />
          <Skeleton height="9rem" />
        </div>
      )}
      {query.isError && <ErrorState title={t('monitor.loadFailed')} body={String(query.error)} />}
      {query.data && <Content data={query.data} />}
    </div>
  )
}
