import { useMemo, useState } from 'react'
import { Button, EmptyState, IconButton, SearchField } from '@/components/ui/primitives'
import { cx } from '@/utils/cx'
import { ConfirmDialog } from '@/components/ui/Modal'
import { useTranslation } from '@/hooks/useTranslation'
import { useDebounced } from '@/hooks/useDebounced'
import { useConversations, type Conversation } from '@/state/conversations'
import { formatRelative } from '@/utils/format'
import './conversations.css'

/** Today, yesterday, this week, earlier — the way a long history is scanned. */
function dayGroup(timestamp: number): 'today' | 'yesterday' | 'week' | 'earlier' {
  const start = new Date()
  start.setHours(0, 0, 0, 0)
  const day = 86_400_000
  if (timestamp >= start.getTime()) return 'today'
  if (timestamp >= start.getTime() - day) return 'yesterday'
  if (timestamp >= start.getTime() - 6 * day) return 'week'
  return 'earlier'
}

export function ConversationSidebar({ onSelect, variant = 'panel' }: { onSelect?: () => void; variant?: 'panel' | 'rail' }) {
  const { t, language } = useTranslation()
  const [term, setTerm] = useState('')
  const [showArchived, setShowArchived] = useState(false)
  const [pendingDelete, setPendingDelete] = useState<string | null>(null)
  const search = useDebounced(term, 200)

  const conversations = useConversations((state) => state.conversations)
  const activeId = useConversations((state) => state.activeId)
  const create = useConversations((state) => state.create)
  const select = useConversations((state) => state.select)
  const remove = useConversations((state) => state.remove)
  const toggleArchive = useConversations((state) => state.toggleArchive)

  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase()
    return conversations
      .filter((c) => c.archived === showArchived)
      .filter((c) => {
        if (!needle) return true
        if (c.title.toLowerCase().includes(needle)) return true
        return c.messages.some((m) => m.content.toLowerCase().includes(needle))
      })
      .sort((a, b) => b.updatedAt - a.updatedAt)
  }, [conversations, search, showArchived])

  const archivedCount = conversations.filter((c) => c.archived).length

  return (
    <aside className={cx('conv-sidebar', variant === 'rail' && 'conv-sidebar--rail')}>
      <div className="conv-sidebar__top">
        <Button
          variant={variant === 'rail' ? 'secondary' : 'primary'}
          icon="pen"
          block
          onClick={() => {
            create()
            onSelect?.()
          }}
        >
          {t('chat.newChat')}
        </Button>
        <SearchField
          placeholder={t('chat.searchConversations')}
          value={term}
          onChange={(event) => setTerm(event.target.value)}
        />
      </div>

      {archivedCount > 0 && (
        <div className="conv-sidebar__tabs">
          <button
            type="button"
            className={cx('conv-sidebar__tab', !showArchived && 'is-active')}
            onClick={() => setShowArchived(false)}
          >
            {t('chat.conversations')}
          </button>
          <button
            type="button"
            className={cx('conv-sidebar__tab', showArchived && 'is-active')}
            onClick={() => setShowArchived(true)}
          >
            {t('chat.archived')} <span className="ltr-nums">{archivedCount}</span>
          </button>
        </div>
      )}

      <div className="conv-sidebar__list">
        {visible.length === 0 ? (
          <EmptyState icon="chat" title={t('chat.noConversations')} />
        ) : (
          visible.map((conversation, index) => (
            <div key={conversation.id}>
            {(index === 0 || dayGroup(visible[index - 1].updatedAt) !== dayGroup(conversation.updatedAt)) && (
              <p className="conv-sidebar__group">{t(`chat.group.${dayGroup(conversation.updatedAt)}`)}</p>
            )}
            <ConversationRow
              conversation={conversation}
              active={conversation.id === activeId}
              relative={formatRelative(conversation.updatedAt, language)}
              onOpen={() => {
                select(conversation.id)
                onSelect?.()
              }}
              onArchive={() => toggleArchive(conversation.id)}
              onDelete={() => setPendingDelete(conversation.id)}
              archiveLabel={conversation.archived ? t('chat.unarchive') : t('chat.archive')}
              deleteLabel={t('chat.deleteConversation')}
            />
            </div>
          ))
        )}
      </div>

      <ConfirmDialog
        open={pendingDelete !== null}
        title={t('chat.deleteConversation')}
        message={t('chat.deleteConfirm')}
        confirmLabel={t('common.delete')}
        destructive
        onCancel={() => setPendingDelete(null)}
        onConfirm={() => {
          if (pendingDelete) remove(pendingDelete)
          setPendingDelete(null)
        }}
      />
    </aside>
  )
}

function ConversationRow({
  conversation,
  active,
  relative,
  onOpen,
  onArchive,
  onDelete,
  archiveLabel,
  deleteLabel,
}: {
  conversation: Conversation
  active: boolean
  relative: string
  onOpen: () => void
  onArchive: () => void
  onDelete: () => void
  archiveLabel: string
  deleteLabel: string
}) {
  return (
    <div className={cx('conv-row', active && 'conv-row--active')}>
      <button type="button" className="conv-row__main" onClick={onOpen}>
        <span className="conv-row__title">{conversation.title || '—'}</span>
        <span className="conv-row__meta ltr-nums">{relative}</span>
      </button>
      <div className="conv-row__actions">
        <IconButton icon="archive" label={archiveLabel} size="sm" onClick={onArchive} />
        <IconButton icon="trash" label={deleteLabel} size="sm" onClick={onDelete} />
      </div>
    </div>
  )
}
