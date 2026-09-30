import { useNavigate } from 'react-router-dom'
import { Badge, IconButton } from '@/components/ui/primitives'
import { cx } from '@/utils/cx'
import { Icon } from '@/components/ui/Icon'
import { useTranslation } from '@/hooks/useTranslation'
import { breadcrumbParts } from '@/utils/format'
import type { SourceReference, WebSource } from '@/types/api'

const TONE = { primary: 'accent', supporting: 'info', related: 'neutral' } as const

/**
 * One cited source.
 *
 * "Open source" goes to the document viewer anchored at the exact section the passage
 * came from, so a citation leads to the text it was drawn from rather than to the top
 * of a long file.
 */
export function SourceCard({
  source,
  highlighted,
  onFocus,
}: {
  source: SourceReference
  highlighted?: boolean
  onFocus?: (citation: number) => void
}) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const crumbs = breadcrumbParts(source.section_path || source.section)

  const open = () => {
    const anchor = source.section_id
      ? `#section-${source.section_id}`
      : source.chunk_id
        ? `#chunk-${source.chunk_id}`
        : ''
    navigate(`/documents/${source.document_id}${anchor}`)
  }

  return (
    <article
      id={`source-${source.citation}`}
      className={cx('source-card', highlighted && 'source-card--highlighted')}
      onMouseEnter={() => onFocus?.(source.citation)}
    >
      <header className="source-card__head">
        <span className="source-card__number">{source.citation}</span>
        <div className="source-card__ident">
          <span className="source-card__file" title={source.filename}>
            {source.title || source.filename}
          </span>
          <Badge tone={TONE[source.tier]}>{t(`source.tier.${source.tier}`)}</Badge>
        </div>
        <IconButton icon="externalLink" label={t('source.openSource')} size="sm" onClick={open} />
      </header>

      {crumbs.length > 0 && (
        <ol className="source-card__crumbs">
          {crumbs.map((crumb, index) => (
            <li key={`${crumb}-${index}`}>
              {index > 0 && <Icon name="chevronEnd" size={11} className="source-card__crumb-sep" />}
              <span>{crumb}</span>
            </li>
          ))}
        </ol>
      )}

      {source.excerpt && <p className="source-card__excerpt">{source.excerpt}</p>}

      <footer className="source-card__meta">
        <span className="ltr-nums">
          {t('source.score')} {source.score.toFixed(2)}
        </span>
        {source.category && <span>{source.category}</span>}
        {source.version && <span className="ltr-nums">{source.version}</span>}
      </footer>
    </article>
  )
}

export function SourceList({
  sources,
  activeCitation,
}: {
  sources: SourceReference[]
  activeCitation?: number | null
}) {
  const { t } = useTranslation()
  if (sources.length === 0) return <p className="chat-note">{t('chat.noSources')}</p>
  return (
    <div className="source-list">
      {sources.map((source) => (
        <SourceCard
          key={`${source.citation}-${source.chunk_id}`}
          source={source}
          highlighted={activeCitation === source.citation}
        />
      ))}
    </div>
  )
}

/**
 * A page from outside the company.
 *
 * Deliberately a different card from `SourceCard`, not the same one with a badge. The
 * distinction between something this company wrote and something found on the internet
 * is the first thing a reader needs, and a variant of the internal card would make it
 * the last thing they notice.
 */
export function WebSourceCard({ source }: { source: WebSource }) {
  const { t } = useTranslation()
  return (
    <a
      className="source-card source-card--web"
      href={source.url}
      target="_blank"
      rel="noopener noreferrer nofollow"
    >
      <div className="source-card__head">
        <Badge tone="warning">{t('chat.webSource')}</Badge>
        <span className="source-card__cite ltr-nums">[{t('chat.webCitationMark')}{source.citation}]</span>
        <span className="source-card__file">{source.title}</span>
        {source.authoritative && (
          <Badge tone="success">{t('chat.authoritative')}</Badge>
        )}
        <Icon name="externalLink" size={13} className="source-card__external" />
      </div>
      <div className="source-card__domain ltr-nums">{source.domain}</div>
      {source.snippet && <p className="source-card__excerpt">{source.snippet}</p>}
    </a>
  )
}

export function WebSourceList({ sources }: { sources: WebSource[] }) {
  if (sources.length === 0) return null
  return (
    <div className="source-list">
      {sources.map((source) => (
        <WebSourceCard key={`${source.citation}-${source.url}`} source={source} />
      ))}
    </div>
  )
}
