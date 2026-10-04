import { useMemo } from 'react'
import { Icon } from '@/components/ui/Icon'
import { useTranslation } from '@/hooks/useTranslation'
import type { FolderEntry } from '@/types/api'
import { cx } from '@/utils/cx'

interface ProjectGroup {
  project: string
  total: number
  kinds: FolderEntry[]
}

/**
 * The library as folders: one row per project, its document types beside it. The tree is
 * built by the server from what uploads and the organizer set — nobody files anything by
 * hand. Choosing a folder narrows the list below; choosing it again opens everything.
 */
export function FolderTree({
  folders,
  project,
  category,
  onChoose,
}: {
  folders: FolderEntry[]
  project: string
  category: string
  onChoose: (project: string, category: string) => void
}) {
  const { t } = useTranslation()
  const groups = useMemo<ProjectGroup[]>(() => {
    const byProject = new Map<string, ProjectGroup>()
    for (const entry of folders) {
      const group = byProject.get(entry.project) ?? { project: entry.project, total: 0, kinds: [] }
      group.total += entry.count
      group.kinds.push(entry)
      byProject.set(entry.project, group)
    }
    // Named projects first, then the documents no project claimed.
    return [...byProject.values()].sort((a, b) => (a.project ? 0 : 1) - (b.project ? 0 : 1) || a.project.localeCompare(b.project))
  }, [folders])

  if (groups.length === 0) return null

  return (
    <nav className="folder-tree" aria-label={t('library.folders')}>
      <div className="folder-tree__head">
        <Icon name="layers" size={15} />
        <span>{t('library.folders')}</span>
        {(project || category) && (
          <button type="button" className="folder-tree__all" onClick={() => onChoose('', '')}>
            {t('library.allFolders')}
          </button>
        )}
      </div>
      <ul className="folder-tree__list">
        {groups.map((group) => {
          const activeProject = project === group.project && !category
          return (
            <li key={group.project || '—'} className="folder-tree__project">
              <button
                type="button"
                className={cx('folder-tree__name', activeProject && 'folder-tree__name--active')}
                onClick={() => onChoose(activeProject ? '' : group.project, '')}
              >
                <span>{group.project || t('library.noProject')}</span>
                <span className="folder-tree__count">{group.total}</span>
              </button>
              <div className="folder-tree__kinds">
                {group.kinds.map((kind) => {
                  const active = project === group.project && category === kind.category
                  return (
                    <button
                      type="button"
                      key={kind.category || '—'}
                      className={cx('folder-tree__kind', active && 'folder-tree__kind--active')}
                      onClick={() => onChoose(active ? '' : group.project, active ? '' : kind.category)}
                    >
                      {kind.category || t('library.noKind')}
                      <span className="folder-tree__count">{kind.count}</span>
                    </button>
                  )
                })}
              </div>
            </li>
          )
        })}
      </ul>
    </nav>
  )
}
