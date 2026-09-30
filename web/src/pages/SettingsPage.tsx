import type { ReactNode } from 'react'
import { Icon, type IconName } from '@/components/ui/Icon'
import { Badge, Button, Card, CardHeader, ErrorState, Field, Segmented, Skeleton } from '@/components/ui/primitives'
import { cx } from '@/utils/cx'
import { useConfig, useHealth } from '@/hooks/useSystem'
import { useTranslation } from '@/hooks/useTranslation'
import { API_BASE } from '@/services/client'
import { usePreferences } from '@/state/preferences'
import type { EffectiveConfig, ServiceHealth } from '@/types/api'
import { formatNumber } from '@/utils/format'
import './settings.css'

const DASH = '—'

interface Row {
  label: string
  value: ReactNode
  mono?: boolean
}

/**
 * A titled block. The configuration block is deliberately untitled: its three cards
 * carry their own headings, and the dictionary has no umbrella term for them.
 */
function Section({ title, action, children }: { title?: string; action?: ReactNode; children: ReactNode }) {
  return (
    <section className="settings__section">
      {(title || action) && (
        <div className="settings__section-head">
          {title && <h2 className="settings__section-title">{title}</h2>}
          {action}
        </div>
      )}
      {children}
    </section>
  )
}

function KeyValues({ rows }: { rows: Row[] }) {
  return (
    <dl className="kv">
      {rows.map((row) => (
        <div key={row.label} className="kv__row">
          <dt className={cx('kv__key', row.mono && 'kv__mono')}>{row.label}</dt>
          <dd className={cx('kv__value', row.mono && 'kv__mono')}>{row.value}</dd>
        </div>
      ))}
    </dl>
  )
}

function ServiceCard({
  icon,
  title,
  reachable,
  url,
  rows,
  note,
}: {
  icon: IconName
  title: string
  reachable: boolean
  url?: string
  rows?: Row[]
  note?: string
}) {
  const { t } = useTranslation()
  return (
    <Card className="service" padded>
      <div className="service__head">
        <span className={cx('service__icon', !reachable && 'service__icon--down')}>
          <Icon name={icon} size={16} />
        </span>
        <span className="service__title">{title}</span>
        <Badge tone={reachable ? 'success' : 'danger'} dot>
          {reachable ? t('settings.reachable') : t('settings.unreachable')}
        </Badge>
      </div>

      {url && (
        <p className="service__url" dir="ltr">
          {url}
        </p>
      )}

      {rows && rows.length > 0 && <KeyValues rows={rows} />}
      {note && <p className="service__note">{note}</p>}
    </Card>
  )
}

function StatCard({ icon, title, stats }: { icon: IconName; title: string; stats: Array<[string, number]> }) {
  const { language } = useTranslation()
  return (
    <Card className="stat-card" padded>
      <div className="stat-card__head">
        <Icon name={icon} size={15} />
        {title}
      </div>
      <div className="stat-card__stats">
        {stats.map(([label, value]) => (
          <div key={label} className="stat-card__stat">
            <span className="stat-card__value">{formatNumber(value, language)}</span>
            <span className="stat-card__label">{label}</span>
          </div>
        ))}
      </div>
    </Card>
  )
}

function CardsSkeleton({ count }: { count: number }) {
  return (
    <div className="service-grid">
      {Array.from({ length: count }, (_, index) => (
        <Skeleton key={index} height="9rem" radius="14px" />
      ))}
    </div>
  )
}

export function SettingsPage() {
  const { t, language } = useTranslation()
  const health = useHealth()
  const config = useConfig()
  const theme = usePreferences((state) => state.theme)
  const setTheme = usePreferences((state) => state.setTheme)
  const setLanguage = usePreferences((state) => state.setLanguage)

  const num = (value: number) => formatNumber(value, language)
  const bool = (value: boolean) => (
    <Badge tone={value ? 'success' : 'neutral'}>{value ? t('common.yes') : t('common.no')}</Badge>
  )

  /** Model name and availability, shown the same way for the LLM and the embedder. */
  const modelRows = (service: ServiceHealth): Row[] => {
    const rows: Row[] = []
    if (service.model) rows.push({ label: t('answer.model'), value: service.model, mono: true })
    if (service.model_available !== undefined) {
      rows.push({ label: t('settings.modelAvailable'), value: bool(service.model_available) })
    }
    return rows
  }

  /** The service's own error, or the reason a reachable host is still not usable. */
  const serviceNote = (service: ServiceHealth): string | undefined => {
    if (service.error) return service.error
    if (service.model_available === false) return t('settings.modelMissing')
    return undefined
  }

  const configGroups = (cfg: EffectiveConfig): Array<{ title: string; rows: Row[] }> => [
    {
      title: t('settings.retrieval'),
      rows: [
        { label: 'top_k', value: num(cfg.top_k), mono: true },
        { label: 'wide_top_k', value: num(cfg.wide_top_k), mono: true },
        { label: 'candidate_pool', value: num(cfg.candidate_pool), mono: true },
        { label: 'score_threshold', value: num(cfg.score_threshold), mono: true },
        { label: 'vector_score_floor', value: num(cfg.vector_score_floor), mono: true },
        { label: 'min_rerank_score', value: num(cfg.min_rerank_score), mono: true },
        { label: 'reranker', value: cfg.reranker || DASH, mono: true },
        { label: 'hybrid.keyword_search', value: bool(cfg.hybrid.keyword_search), mono: true },
        { label: 'hybrid.entity_retrieval', value: bool(cfg.hybrid.entity_retrieval), mono: true },
        { label: 'hybrid.expansion', value: bool(cfg.hybrid.expansion), mono: true },
      ],
    },
    {
      title: t('settings.chunking'),
      rows: [
        { label: 'chunk_size', value: num(cfg.chunk_size), mono: true },
        { label: 'chunk_overlap', value: num(cfg.chunk_overlap), mono: true },
        {
          label: 'supported_extensions',
          value: (
            <span className="config__extensions">
              {cfg.supported_extensions.map((extension) => (
                <Badge key={extension} tone="neutral">
                  {extension}
                </Badge>
              ))}
            </span>
          ),
          mono: true,
        },
      ],
    },
    {
      title: t('settings.context'),
      rows: [
        { label: 'max_context_chars', value: num(cfg.max_context_chars), mono: true },
        { label: 'ollama_num_ctx', value: num(cfg.ollama_num_ctx), mono: true },
      ],
    },
  ]

  return (
    <div className="page settings">
      <header className="page__head">
        <div>
          <h1 className="page__title">{t('settings.title')}</h1>
          <p className="page__subtitle">{t('settings.subtitle')}</p>
        </div>
      </header>

      <Section
        title={t('settings.services')}
        action={
          <Button
            size="sm"
            icon="refresh"
            loading={health.isFetching}
            onClick={() => void health.refetch()}
          >
            {t('common.refresh')}
          </Button>
        }
      >
        {health.isLoading ? (
          <CardsSkeleton count={4} />
        ) : health.isError || !health.data ? (
          <ErrorState
            title={t('errors.title')}
            body={health.error instanceof Error ? health.error.message : t('errors.network')}
            action={
              <Button size="sm" icon="refresh" onClick={() => void health.refetch()}>
                {t('common.retry')}
              </Button>
            }
          />
        ) : (
          <div className="service-grid">
            <ServiceCard
              icon="sparkles"
              title={t('settings.llm')}
              reachable={health.data.ollama.reachable}
              url={health.data.ollama.url}
              rows={modelRows(health.data.ollama)}
              note={serviceNote(health.data.ollama)}
            />
            <ServiceCard
              icon="cpu"
              title={t('settings.embeddings')}
              reachable={health.data.embeddings.reachable}
              url={health.data.embeddings.url}
              rows={modelRows(health.data.embeddings)}
              note={serviceNote(health.data.embeddings)}
            />
            <ServiceCard
              icon="database"
              title={t('settings.vectorDb')}
              reachable={health.data.qdrant.reachable}
              url={health.data.qdrant.url}
              rows={[
                {
                  label: t('settings.collection'),
                  value: health.data.qdrant.collection ?? DASH,
                  mono: true,
                },
                {
                  label: t('settings.points'),
                  value:
                    health.data.qdrant.points === undefined ? DASH : num(health.data.qdrant.points),
                },
              ]}
              note={serviceNote(health.data.qdrant)}
            />
            <ServiceCard
              icon="shield"
              title={t('settings.api')}
              reachable={health.data.status === 'ok'}
              url={API_BASE || window.location.origin}
              note={health.data.status === 'ok' ? undefined : t('library.degraded')}
            />
          </div>
        )}
      </Section>

      <Section title={t('settings.indexes')}>
        {health.isLoading ? (
          <CardsSkeleton count={3} />
        ) : health.data ? (
          <div className="service-grid">
            <StatCard
              icon="search"
              title={t('settings.keywordIndex')}
              stats={[
                [t('library.chunks'), health.data.keyword_index.chunks],
                [t('settings.terms'), health.data.keyword_index.terms],
              ]}
            />
            <StatCard
              icon="layers"
              title={t('settings.knowledgeIndex')}
              stats={[
                [t('library.sections'), health.data.knowledge_index.sections],
                [t('library.entities'), health.data.knowledge_index.entities],
                [t('library.chunks'), health.data.knowledge_index.chunks],
              ]}
            />
            <StatCard
              icon="archive"
              title={t('settings.cache')}
              stats={[
                [t('settings.hits'), health.data.embedding_cache.hits],
                [t('settings.misses'), health.data.embedding_cache.misses],
              ]}
            />
          </div>
        ) : null}
      </Section>

      <Section>
        {config.isLoading ? (
          <CardsSkeleton count={3} />
        ) : config.isError || !config.data ? (
          <ErrorState
            title={t('errors.title')}
            body={config.error instanceof Error ? config.error.message : t('errors.network')}
            action={
              <Button size="sm" icon="refresh" onClick={() => void config.refetch()}>
                {t('common.retry')}
              </Button>
            }
          />
        ) : (
          <>
            <div className="config-grid">
              {configGroups(config.data).map((group) => (
                <Card key={group.title} className="config-group">
                  <CardHeader title={group.title} />
                  <div className="config-group__body">
                    <KeyValues rows={group.rows} />
                  </div>
                </Card>
              ))}
            </div>
            <p className="settings__hint">
              <Icon name="shield" size={13} />
              {t('settings.noSecrets')}
            </p>
          </>
        )}
      </Section>

      <Section title={t('settings.appearance')}>
        <Card className="appearance" padded>
          <Field label={t('theme.label')}>
            <Segmented
              label={t('theme.label')}
              value={theme}
              onChange={setTheme}
              options={[
                { value: 'light', label: t('theme.light'), icon: 'sun' },
                { value: 'dark', label: t('theme.dark'), icon: 'moon' },
                { value: 'system', label: t('theme.system'), icon: 'monitor' },
              ]}
            />
          </Field>

          <Field label={t('language.label')}>
            <Segmented
              label={t('language.label')}
              value={language}
              onChange={setLanguage}
              options={[
                { value: 'ar', label: t('language.arabic'), icon: 'globe' },
                { value: 'en', label: t('language.english'), icon: 'globe' },
              ]}
            />
          </Field>
        </Card>
      </Section>
    </div>
  )
}
