import { fileExtension } from './format'

/**
 * A project folder read before anything is sent: which files go, which stay, and why.
 *
 * The folder that was picked is the project; the first folder inside it is the kind of
 * document (Letters, Payments, Variations…); the path below the project is kept as the
 * file's folder. A file directly in the project folder has no kind.
 */

export type SkipReason = 'type' | 'size' | 'empty' | 'system'

export interface PlannedFile {
  file: File
  /** The path inside the project folder, for the report. */
  path: string
  /** The first folder inside the project, used as the document type. */
  kind: string
  /** Every folder between the project and the file. */
  folder: string
  skip?: SkipReason
}

export interface FolderPlan {
  project: string
  files: PlannedFile[]
}

/** Files nobody means to upload: Office lock files, Windows and macOS folder metadata. */
function isSystemFile(name: string): boolean {
  const lower = name.toLowerCase()
  return lower.startsWith('~$') || lower.startsWith('.') || lower === 'thumbs.db' || lower === 'desktop.ini'
}

export function relativePath(file: File): string {
  return (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name
}

export function planFolder(files: File[], extensions: string[], maxSize: number): FolderPlan {
  const planned: PlannedFile[] = []
  let project = ''
  for (const file of files) {
    const parts = relativePath(file).split('/').filter(Boolean)
    if (!project && parts.length > 1) project = parts[0]
    const inside = parts.length > 1 ? parts.slice(1) : parts
    const folders = inside.slice(0, -1)
    const entry: PlannedFile = {
      file,
      path: inside.join('/'),
      kind: folders[0] ?? '',
      folder: folders.join('/'),
    }
    if (isSystemFile(file.name)) entry.skip = 'system'
    else if (!extensions.includes(fileExtension(file.name))) entry.skip = 'type'
    else if (file.size === 0) entry.skip = 'empty'
    else if (file.size > maxSize) entry.skip = 'size'
    planned.push(entry)
  }
  planned.sort((a, b) => a.path.localeCompare(b.path))
  return { project, files: planned }
}

export type Outcome = 'uploaded' | 'duplicate' | 'failed'

export interface KindSummary {
  kind: string
  total: number
  toUpload: number
  skipped: number
  uploaded: number
  duplicate: number
  failed: number
}

/** One row per document type, in the order the folders were listed. */
export function summarize(plan: FolderPlan, outcomes: Map<PlannedFile, Outcome>): KindSummary[] {
  const rows = new Map<string, KindSummary>()
  for (const entry of plan.files) {
    const row = rows.get(entry.kind) ?? {
      kind: entry.kind, total: 0, toUpload: 0, skipped: 0, uploaded: 0, duplicate: 0, failed: 0,
    }
    row.total += 1
    if (entry.skip) row.skipped += 1
    else row.toUpload += 1
    const outcome = outcomes.get(entry)
    if (outcome) row[outcome] += 1
    rows.set(entry.kind, row)
  }
  return [...rows.values()]
}

/** Extensions that were left behind, most common first: what to convert, at a glance. */
export function skippedTypes(plan: FolderPlan): Array<{ extension: string; count: number }> {
  const counts = new Map<string, number>()
  for (const entry of plan.files) {
    if (entry.skip !== 'type') continue
    const extension = fileExtension(entry.file.name) || '—'
    counts.set(extension, (counts.get(extension) ?? 0) + 1)
  }
  return [...counts.entries()].map(([extension, count]) => ({ extension, count })).sort((a, b) => b.count - a.count)
}
