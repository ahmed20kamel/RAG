import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { Icon } from '@/components/ui/Icon'
import { Badge, Button, Card, CardHeader, Field, Input, Progress } from '@/components/ui/primitives'
import { documentKeys } from '@/hooks/useDocuments'
import { useTranslation } from '@/hooks/useTranslation'
import { ApiError } from '@/services/client'
import { documentsApi } from '@/services/documents'
import { toast } from '@/state/toasts'
import {
  planFolder,
  skippedTypes,
  summarize,
  type FolderPlan,
  type Outcome,
  type PlannedFile,
} from '@/utils/folderPlan'

interface Problem {
  path: string
  reason: string
}

/**
 * A whole project folder at once. Nothing is sent until the plan has been seen: what
 * goes, what stays and why, grouped by the folder each file sits in. Afterwards the same
 * table reports what happened, and every file that did not make it is listed by path.
 *
 * One file at a time, like the single-file queue: the server indexes one document at a
 * time, and parallel uploads only make every file slower.
 */
export function FolderUpload({ extensions, maxSize }: { extensions: string[]; maxSize: number }) {
  const { t } = useTranslation()
  const client = useQueryClient()
  const input = useRef<HTMLInputElement>(null)

  const [plan, setPlan] = useState<FolderPlan | null>(null)
  const [project, setProject] = useState('')
  const [outcomes, setOutcomes] = useState<Map<PlannedFile, Outcome>>(new Map())
  const [problems, setProblems] = useState<Problem[]>([])
  const [running, setRunning] = useState(false)
  const [done, setDone] = useState(0)

  // `webkitdirectory` is not in React's attribute list; set it on the element itself.
  useEffect(() => {
    input.current?.setAttribute('webkitdirectory', '')
    input.current?.setAttribute('directory', '')
  }, [])

  const toUpload = useMemo(() => plan?.files.filter((entry) => !entry.skip) ?? [], [plan])
  const rows = useMemo(() => (plan ? summarize(plan, outcomes) : []), [plan, outcomes])
  const leftBehind = useMemo(() => (plan ? skippedTypes(plan) : []), [plan])
  const finished = plan !== null && !running && outcomes.size > 0

  function choose(files: FileList | null) {
    if (!files || files.length === 0) return
    const next = planFolder(Array.from(files), extensions, maxSize)
    setPlan(next)
    setProject(next.project)
    setOutcomes(new Map())
    setDone(0)
    setProblems(
      next.files
        .filter((entry) => entry.skip && entry.skip !== 'system')
        .map((entry) => ({ path: entry.path, reason: t(`upload.rejected.${entry.skip as 'type' | 'size' | 'empty'}`) })),
    )
  }

  async function start() {
    if (!plan || toUpload.length === 0) return
    setRunning(true)
    const results = new Map<PlannedFile, Outcome>()
    const failures: Problem[] = []
    for (const entry of toUpload) {
      try {
        await documentsApi.upload(entry.file, {
          project: project.trim() || undefined,
          folder: entry.folder || undefined,
          category: entry.kind || undefined,
        })
        results.set(entry, 'uploaded')
      } catch (error) {
        const duplicate = error instanceof ApiError && error.status === 409
        results.set(entry, duplicate ? 'duplicate' : 'failed')
        if (!duplicate) {
          failures.push({ path: entry.path, reason: error instanceof Error ? error.message : t('errors.generic') })
        }
      }
      setOutcomes(new Map(results))
      setDone((value) => value + 1)
    }
    setProblems((previous) => [...previous, ...failures])
    setRunning(false)
    void client.invalidateQueries({ queryKey: documentKeys.all })
    const uploaded = [...results.values()].filter((value) => value === 'uploaded').length
    toast.success(t('upload.folder.doneToast', { count: uploaded }), project)
  }

  function reset() {
    setPlan(null)
    setOutcomes(new Map())
    setProblems([])
    setDone(0)
  }

  return (
    <Card>
      <CardHeader
        title={t('upload.folder.title')}
        action={
          <div className="upload-actions">
            {plan && !running && (
              <Button variant="ghost" size="sm" onClick={reset}>
                {t('upload.folder.another')}
              </Button>
            )}
            {!plan && (
              <Button variant="primary" size="sm" icon="upload" onClick={() => input.current?.click()}>
                {t('upload.folder.choose')}
              </Button>
            )}
            {plan && !finished && (
              <Button
                variant="primary"
                size="sm"
                icon="upload"
                loading={running}
                disabled={toUpload.length === 0 || !project.trim()}
                onClick={() => void start()}
              >
                {t('upload.folder.start', { count: toUpload.length })}
              </Button>
            )}
          </div>
        }
      />

      <input
        ref={input}
        type="file"
        multiple
        hidden
        aria-label={t('upload.folder.choose')}
        onChange={(event) => {
          choose(event.target.files)
          event.target.value = ''
        }}
      />

      {!plan && <p className="folder-upload__hint">{t('upload.folder.hint')}</p>}

      {plan && (
        <div className="folder-upload">
          <Field label={t('upload.folder.project')} htmlFor="folder-project">
            <Input
              id="folder-project"
              value={project}
              disabled={running || finished}
              onChange={(event) => setProject(event.target.value)}
            />
          </Field>

          {running && (
            <div className="folder-upload__progress">
              <Progress value={toUpload.length ? done / toUpload.length : 0} label={t('upload.uploading')} />
              <span>{t('upload.folder.progress', { done, total: toUpload.length })}</span>
            </div>
          )}

          <div className="folder-upload__table" role="table">
            <div className="folder-upload__row folder-upload__row--head" role="row">
              <span role="columnheader">{t('upload.folder.kind')}</span>
              <span role="columnheader">{t('upload.folder.files')}</span>
              <span role="columnheader">{finished ? t('upload.folder.uploaded') : t('upload.folder.willUpload')}</span>
              <span role="columnheader">{finished ? t('upload.folder.duplicate') : ''}</span>
              <span role="columnheader">{t('upload.folder.skipped')}</span>
            </div>
            {rows.map((row) => (
              <div className="folder-upload__row" role="row" key={row.kind || '—'}>
                <span role="cell" className="folder-upload__kind">{row.kind || t('upload.folder.noKind')}</span>
                <span role="cell">{row.total}</span>
                <span role="cell">{finished ? row.uploaded : row.toUpload}</span>
                <span role="cell">{finished && row.duplicate ? row.duplicate : ''}</span>
                <span role="cell">
                  {row.skipped + row.failed > 0 ? (
                    <Badge tone={row.failed ? 'danger' : 'neutral'}>{row.skipped + row.failed}</Badge>
                  ) : (
                    ''
                  )}
                </span>
              </div>
            ))}
          </div>

          {leftBehind.length > 0 && (
            <p className="upload-note">
              <Icon name="info" size={14} />
              <span>
                {t('upload.folder.leftBehind')}{' '}
                {leftBehind.map(({ extension, count }) => `${extension} (${count})`).join(' · ')}
              </span>
            </p>
          )}

          {problems.length > 0 && (
            <details className="folder-upload__problems">
              <summary>{t('upload.folder.problems', { count: problems.length })}</summary>
              <ul>
                {problems.map((problem) => (
                  <li key={problem.path}>
                    <span className="folder-upload__path">{problem.path}</span>
                    <span className="folder-upload__reason">{problem.reason}</span>
                  </li>
                ))}
              </ul>
            </details>
          )}

          {finished && (
            <p className="upload-note">
              <Icon name="info" size={14} />
              <span>
                {t('upload.folder.processing')}{' '}
                <Link to="/library">{t('upload.folder.openLibrary')}</Link>
              </span>
            </p>
          )}
        </div>
      )}
    </Card>
  )
}
