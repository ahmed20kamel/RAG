import { describe, expect, it } from 'vitest'
import { planFolder, skippedTypes, summarize, type Outcome, type PlannedFile } from '@/utils/folderPlan'

function file(path: string, size = 10): File {
  const name = path.split('/').pop() as string
  const f = new File([new Uint8Array(size)], name)
  Object.defineProperty(f, 'webkitRelativePath', { value: path })
  return f
}

const EXT = ['.pdf', '.docx', '.xlsx']
const MAX = 100

describe('planFolder', () => {
  const plan = planFolder(
    [
      file('Villa A/Letters/L-001.pdf'),
      file('Villa A/Letters/2024/L-002.pdf'),
      file('Villa A/Payments/IPC 01.xlsx'),
      file('Villa A/Drawings/A-101.dwg'),
      file('Villa A/Drawings/huge.pdf', 500),
      file('Villa A/Letters/empty.pdf', 0),
      file('Villa A/Letters/~$L-001.docx'),
      file('Villa A/Thumbs.db'),
      file('Villa A/Key handover.pdf'),
    ],
    EXT,
    MAX,
  )
  const byPath = (p: string) => plan.files.find((f) => f.path === p) as PlannedFile

  it('takes the project from the folder that was picked', () => {
    expect(plan.project).toBe('Villa A')
  })

  it('takes the type from the first folder inside it and keeps the full folder', () => {
    expect(byPath('Letters/2024/L-002.pdf')).toMatchObject({ kind: 'Letters', folder: 'Letters/2024' })
    expect(byPath('Payments/IPC 01.xlsx')).toMatchObject({ kind: 'Payments', folder: 'Payments' })
    expect(byPath('Payments/IPC 01.xlsx').skip).toBeUndefined()
  })

  it('gives a file directly in the project folder no type', () => {
    expect(byPath('Key handover.pdf')).toMatchObject({ kind: '', folder: '' })
  })

  it('says why each file stays behind', () => {
    expect(byPath('Drawings/A-101.dwg').skip).toBe('type')
    expect(byPath('Drawings/huge.pdf').skip).toBe('size')
    expect(byPath('Letters/empty.pdf').skip).toBe('empty')
    expect(byPath('Letters/~$L-001.docx').skip).toBe('system')
    expect(byPath('Thumbs.db').skip).toBe('system')
  })

  it('summarises per type, before and after the upload', () => {
    const outcomes = new Map<PlannedFile, Outcome>([
      [byPath('Letters/L-001.pdf'), 'uploaded'],
      [byPath('Letters/2024/L-002.pdf'), 'duplicate'],
    ])
    const letters = summarize(plan, outcomes).find((r) => r.kind === 'Letters')
    expect(letters).toMatchObject({ total: 4, toUpload: 2, skipped: 2, uploaded: 1, duplicate: 1, failed: 0 })
  })

  it('lists the formats left behind', () => {
    expect(skippedTypes(plan)).toEqual([{ extension: '.dwg', count: 1 }])
  })
})
