import { describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api.ts'
import type { UploadItem, UploadResult } from '../api.ts'
import {
  UploadProgress,
  UploadStopped,
  batches,
  isAbort,
  screen,
  sendInBatches,
  shareWords,
  sharesOf,
  uploadStopped,
  wholeOf,
} from './uploads.ts'
import type { UploadState } from './uploads.ts'

const item = (name: string, size = 10, folder = ''): UploadItem => ({ file: new File(['x'.repeat(size)], name), folder })
const limits = { files: 2, request_bytes: 25, file_bytes: 20 }
const names = (items: UploadItem[]) => items.map(({ file, folder }) => (folder ? `${folder}/${file.name}` : file.name))

describe('what an upload leaves out before sending', () => {
  it('is what the server would reject, named by its path, and what a folder holds hidden, unsaid', () => {
    const { items, left } = screen(
      [
        item('a.pdf'),
        item('b.zip', 10, 'Invoices'),
        item('big.pdf', 21, 'Invoices'),
        item('.DS_Store', 10, 'Invoices'),
        item('Thumbs.db', 10, 'Invoices/2026'),
        item('config.md', 10, 'Invoices/.git'),
        item('c.MD', 10, 'Invoices/2026'),
        // A hidden file chosen by itself is screened as any other, and has no extension.
        item('.env'),
      ],
      limits,
      ['Invoices/locked.pdf'],
    )
    expect(names(items)).toEqual(['a.pdf', 'Invoices/2026/c.MD'])
    const unsupported = 'Unsupported type. Use .csv, .docx, .jpeg, .jpg, .md, .pdf, .png, .txt, .webp, .xlsx.'
    expect(left).toEqual([
      { filename: 'Invoices/locked.pdf', reason: 'Your browser could not read it from your computer.', limit: null },
      { filename: 'Invoices/b.zip', reason: unsupported, limit: null },
      { filename: 'Invoices/big.pdf', reason: 'Larger than 0 MB.', limit: null },
      { filename: '.env', reason: unsupported, limit: null },
    ])
  })

  it('words a file larger than a request may carry by that, when it is the smaller limit', () => {
    const mb = 1024 * 1024
    const { left } = screen([item('a.pdf', 3 * mb)], { files: 100, request_bytes: 2 * mb, file_bytes: 100 * mb })
    expect(left[0].reason).toBe('Larger than 2 MB.')
  })
})

describe('the requests of an upload', () => {
  it('hold as many files and bytes as one may, in order', () => {
    const sizes = [10, 10, 10, 20, 5, 25, 1]
    const requests = batches(sizes.map((size, n) => item(`${n}.md`, size)), limits)
    expect(requests.map((files) => files.map(({ file }) => file.size))).toEqual([[10, 10], [10], [20, 5], [25], [1]])
  })

  it('are sent one after another, each reporting how far it has gone, and add up', async () => {
    const items = ['a', 'b', 'c'].map((name) => item(`${name}.md`))
    const reports: UploadState[] = []
    const sent: string[][] = []
    const result = await sendInBatches(items, limits, async (batch, onProgress) => {
      sent.push(names(batch))
      onProgress(100, 100)
      return { documents: [], rejected: [{ filename: batch[0].file.name, reason: 'No.', limit: null }] }
    }, (state) => reports.push(state))
    expect(sent).toEqual([['a.md', 'b.md'], ['c.md']])
    expect(reports.map(({ answered, sending, loaded }) => [answered, sending, loaded])).toEqual([
      [0, 2, 0],
      [0, 2, 100],
      [2, 1, 0],
      [2, 1, 100],
    ])
    expect(result.rejected.map(({ filename }) => filename)).toEqual(['a.md', 'c.md'])
  })

  it('stop at the first that fails, which keeps what went in and the files not sent', async () => {
    const items = ['a', 'b', 'c', 'd', 'e'].map((name) => item(`${name}.md`))
    const done: UploadResult = { documents: [], rejected: [{ filename: 'a.md', reason: 'No.', limit: null }] }
    const refusal = new ApiError(429, 'Too many uploads.')
    const send = vi.fn().mockResolvedValueOnce(done).mockRejectedValueOnce(refusal)
    const stopped = await sendInBatches(items, limits, send, () => {}).catch((error: unknown) => error)
    expect(stopped).toBeInstanceOf(UploadStopped)
    const { error, result, unsent } = stopped as UploadStopped
    expect(error).toBe(refusal)
    expect(result.rejected).toEqual(done.rejected)
    expect(names(unsent)).toEqual(['c.md', 'd.md', 'e.md'])
    expect(send).toHaveBeenCalledTimes(2)
    expect(isAbort(error)).toBe(false)
    expect(isAbort(new DOMException('Stopped.', 'AbortError'))).toBe(true)
    expect(uploadStopped(3)).toBe('The upload was stopped: the last 3 files did not go in.')
  })
})

describe('how far an upload has gone', () => {
  // Two requests: a, b (10 and 30 bytes) answered, then c, d, e (20, 20 and 40) on their way.
  const items = [item('a.md', 10), item('b.md', 30), item('c.md', 20), item('d.md', 20), item('e.md', 40)]
  const state = (loaded: number, total: number): UploadState => ({ items, answered: 2, sending: 3, loaded, total })

  it('takes a request body’s share from its files in turn', () => {
    // Half of a body of 160 bytes, multipart's lines included: 40 of its 80 bytes of files.
    expect(sharesOf(state(80, 160))).toEqual([1, 1, 1, 1, 0])
    expect(sharesOf(state(60, 160))).toEqual([1, 1, 1, 0.5, 0])
    expect(sharesOf(state(0, 0))).toEqual([1, 1, 0, 0, 0])
    expect(sharesOf(state(60, 160)).map(shareWords)).toEqual(['Sent', 'Sent', 'Sent', '50 %', 'Waiting'])
  })

  it('says so as a whole, and that the server stores what it was sent', () => {
    expect(wholeOf(state(60, 160))).toEqual({
      percent: 58,
      sent: 3,
      storing: false,
      line: 'Sent 3 of 5 files.',
      bytes: '70 bytes of 120 bytes',
    })
    const last = { items: items.slice(0, 2), answered: 0, sending: 2, loaded: 90, total: 90 }
    expect(wholeOf(last)).toMatchObject({ percent: 100, storing: true, line: 'Sent all 2 files; Emudoc is storing them.' })
    const one = { items: items.slice(0, 1), answered: 0, sending: 1, loaded: 90, total: 90 }
    expect(wholeOf(one).line).toBe('Sent the file; Emudoc is storing it.')
  })

  it('is followed by its panel alone', () => {
    const progress = new UploadProgress()
    const heard = vi.fn()
    const stop = progress.subscribe(heard)
    progress.set(state(0, 0))
    expect(progress.get()?.answered).toBe(2)
    stop()
    progress.set(null)
    expect(heard).toHaveBeenCalledOnce()
    expect(progress.get()).toBeNull()
  })
})
