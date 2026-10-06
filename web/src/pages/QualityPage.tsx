import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Icon, type IconName } from '@/components/ui/Icon'
import { Card, CardHeader, EmptyState, ErrorState, Segmented, Skeleton } from '@/components/ui/primitives'
import { can, useCurrentUser } from '@/hooks/useAuth'
import { useTranslation } from '@/hooks/useTranslation'
import { monitoringApi, type QualityReport, type QualityWindow } from '@/services/monitoring'
import { formatNumber, formatPercent } from '@/utils/format'
import { cx } from '@/utils/cx'
import './quality.css'

type Days = '30' | '90'

/**
 * How good the answers are and how much the system has learned, over time.
 *
 * Every figure is counted from what happened — questions answered, figures verified,
 * thumbs given, corrections taken on — and shown for this week against the one before,
 * so an improvement or a slide is visible as a number, not an impression.
 */
export function QualityPage() {
  const { t } = useTranslation()
  const { data: user } = useCurrentUser()
  const [days, setDays] = useState<Days>('30')
  const allowed = can(user, 'system.monitor')
  const query = useQuery({
    queryKey: ['quality', days],
    queryFn: () => monitoringApi.quality(Number(days)),
    enabled: allowed,
    refetchInterval: 120_000,
  })

  if (!allowed) {
    return (
      <div className="page">
        <EmptyState icon="shield" title={t('quality.title')} body={t('monitor.adminOnly')} />
      </div>
    )
  }

  return (
    <div className="page">
      <header className="page__head">
        <div>
          <h1 className="page__title">{t('quality.title')}</h1>
          <p className="quality-subtitle">{t('quality.subtitle')}</p>
        </div>
        <Segmented<Days>
          label={t('monitor.window')}
          value={days}
          onChange={setDays}
          options={[
            { value: '30', label: t('quality.days30') },
            { value: '90', label: t('quality.days90') },
          ]}
        />
      </header>
      {query.isLoading && (
        <div className="quality-kpis">
          {[0, 1, 2, 3].map((i) => <Skeleton key={i} height="7rem" />)}
        </div>
      )}
      {query.isError && <ErrorState title={t('monitor.loadFailed')} body={String(query.error)} />}
      {query.data && <Report data={query.data} />}
    </div>
  )
}

function Report({ data }: { data: QualityReport }) {
  const { t, language } = useTranslation()
  const { this_week: now, last_week: before, totals, learned } = data
  const percent = (value: number | null) => (value === null ? '—' : formatPercent(value, language))

  return (
    <>
      <section className="quality-kpis">
        <Kpi icon="check" label={t('quality.answerRate')} hint={t('quality.answerRateHint')}
          value={percent(now.answer_rate)} delta={delta(now.answer_rate, before.answer_rate)} />
        <Kpi icon="shield" label={t('quality.verified')} hint={t('quality.verifiedHint')}
          value={percent(now.verified_rate)} delta={delta(now.verified_rate, before.verified_rate)} />
        <Kpi icon="thumbUp" label={t('quality.satisfaction')} hint={t('quality.satisfactionHint', { up: now.up, down: now.down })}
          value={percent(now.satisfaction)} delta={delta(now.satisfaction, before.satisfaction)} />
        <Kpi icon="clock" label={t('quality.speed')} hint={t('quality.speedHint')}
          value={now.median_seconds === null ? '—' : t('quality.seconds', { n: formatNumber(now.median_seconds, language) })}
          delta={delta(now.median_seconds, before.median_seconds, true)} unit="s" />
      </section>

      <Card padded>
        <CardHeader title={t('quality.trend')} />
        <Trend series={data.series} />
        <p className="quality-legend">
          <span className="quality-legend__key quality-legend__key--answer" /> {t('quality.answerRate')}
          <span className="quality-legend__key quality-legend__key--verified" /> {t('quality.verified')}
          <span className="quality-legend__key quality-legend__key--volume" /> {t('quality.volume')}
        </p>
      </Card>

      <div className="quality-grid">
        <Card padded>
          <CardHeader title={t('quality.learnedTitle')} />
          <ul className="quality-learned">
            <Learned icon="pen" label={t('quality.corrections')} value={learned.corrections} />
            <Learned icon="tag" label={t('quality.terms')} value={learned.terms} />
            <Learned icon="refresh" label={t('quality.wordings')} value={learned.wordings} />
            <Learned icon="chat" label={t('quality.followUps')} value={learned.follow_ups} />
            <Learned icon="database" label={t('quality.remembered')} value={totals.remembered} />
            <Learned icon="inbox" label={t('quality.sharedPending')} value={learned.shared_pending} />
          </ul>
          <p className="quality-muted">
            {t('quality.activePersonal', { n: formatNumber(learned.active_personal, language) })} ·{' '}
            {t('quality.period', { questions: formatNumber(totals.questions, language), users: formatNumber(totals.users, language) })}
          </p>
        </Card>

        <Card padded>
          <CardHeader title={t('quality.toFix')} />
          {data.unanswered.length === 0 ? (
            <p className="quality-muted">{t('quality.noMisses')}</p>
          ) : (
            <ol className="quality-list">
              {data.unanswered.map((item) => (
                <li key={item.question}>
                  <span>{item.question}</span>
                  {item.count > 1 && <span className="quality-count">×{formatNumber(item.count, language)}</span>}
                </li>
              ))}
            </ol>
          )}
          <p className="quality-muted">{t('quality.toFixHint')}</p>
        </Card>
      </div>

      <Card padded>
        <CardHeader title={t('quality.disliked')} />
        {data.disliked.length === 0 ? (
          <p className="quality-muted">{t('quality.noDislikes')}</p>
        ) : (
          <ul className="quality-disliked">
            {data.disliked.map((item) => (
              <li key={item.date + item.question}>
                <strong>{item.question}</strong>
                <span>{item.answer}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </>
  )
}

/** Change against last week, in points for rates, as a ratio for times. */
function delta(now: number | null, before: number | null, lowerIsBetter = false) {
  if (now === null || before === null) return null
  const change = now - before
  if (Math.abs(change) < 1e-9) return { change: 0, good: true }
  return { change, good: lowerIsBetter ? change < 0 : change > 0 }
}

function Kpi({
  icon, label, hint, value, delta: d, unit,
}: {
  icon: IconName
  label: string
  hint: string
  value: string
  delta: { change: number; good: boolean } | null
  unit?: 's'
}) {
  const { t, language } = useTranslation()
  // An arrow and a size, not a sign: "-4ث" read badly inside Arabic text.
  const shown = d === null ? null
    : `${d.change > 0 ? '▲' : '▼'} ${unit === 's'
      ? `${formatNumber(Math.abs(Math.round(d.change * 10) / 10), language)}${t('quality.secondsShort')}`
      : `${formatNumber(Math.abs(Math.round(d.change * 100)), language)}${t('quality.points')}`}`
  return (
    <div className="quality-kpi">
      <span className="quality-kpi__icon"><Icon name={icon} size={18} /></span>
      <span className="quality-kpi__label">{label}</span>
      <strong className="quality-kpi__value">{value}</strong>
      {shown !== null && (
        <span className={cx('quality-kpi__delta', d!.change === 0 ? '' : d!.good ? 'is-good' : 'is-bad')}>
          {d!.change === 0 ? t('quality.noChange') : t('quality.vsLastWeek', { change: shown })}
        </span>
      )}
      <span className="quality-kpi__hint">{hint}</span>
    </div>
  )
}

function Learned({ icon, label, value }: { icon: IconName; label: string; value: number }) {
  const { language } = useTranslation()
  return (
    <li>
      <Icon name={icon} size={15} />
      <span>{label}</span>
      <strong>{formatNumber(value, language)}</strong>
    </li>
  )
}

/** Answer rate and verified rate as lines, daily volume as bars, on one quiet chart. */
function Trend({ series }: { series: QualityWindow[] }) {
  const { t } = useTranslation()
  const width = 760
  const height = 200
  const pad = 24
  const peak = Math.max(1, ...series.map((d) => d.total))
  const step = series.length > 1 ? (width - pad * 2) / (series.length - 1) : 0
  const x = (i: number) => pad + i * step
  const y = (ratio: number) => height - pad - ratio * (height - pad * 2)
  const line = (key: 'answer_rate' | 'verified_rate') =>
    series
      .map((d, i) => (d[key] === null ? null : `${x(i).toFixed(1)},${y(d[key] as number).toFixed(1)}`))
      .filter(Boolean)
      .join(' ')
  if (series.every((d) => d.total === 0)) return <p className="quality-muted">{t('monitor.noTraffic')}</p>
  return (
    // Drawn left to right whatever the page direction: time on a chart reads that way, and
    // inside a right-to-left page the axis labels were mirrored and cut off.
    <svg className="quality-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={t('quality.trend')} direction="ltr">
      {[0, 0.5, 1].map((r) => (
        <g key={r}>
          <line x1={pad} x2={width - pad} y1={y(r)} y2={y(r)} className="quality-chart__grid" />
          <text x={2} y={y(r) + 4} className="quality-chart__axis">{`${Math.round(r * 100)}%`}</text>
        </g>
      ))}
      {series.map((d, i) => (
        <rect key={d.date} x={x(i) - 3} width={6} y={y((d.total / peak) * 0.35)}
          height={height - pad - y((d.total / peak) * 0.35)} className="quality-chart__bar">
          <title>{`${d.date}: ${d.total}`}</title>
        </rect>
      ))}
      <polyline points={line('answer_rate')} className="quality-chart__line quality-chart__line--answer" />
      <polyline points={line('verified_rate')} className="quality-chart__line quality-chart__line--verified" />
      <text x={pad} y={height - 4} className="quality-chart__axis">{series[0]?.date}</text>
      <text x={width - pad} y={height - 4} textAnchor="end" className="quality-chart__axis">{series[series.length - 1]?.date}</text>
    </svg>
  )
}
