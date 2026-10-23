import { describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api.ts'
import type { UploadItem, UploadResult, UploadStatus } from '../api.ts'
import { REFRESH, WAITING_REFRESH } from './listing.ts'
import {
  FOLLOWED_A_REQUEST,
  UploadProgress,
  UploadStopped,
  batches,
  followAfter,
  followedBatches,
  isAbort,
  screen,
  sendInBatches,
  settled,
  shareWords,
  sharesOf,
  standingOf,
  summed,
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
      // The second request keeps no file, and so is no upload on the server.
      return {
        documents: [],
        rejected: [{ filename: batch[0].file.name, reason: 'No.', limit: null }],
        upload_id: sent.length === 1 ? 7 : null,
      }
    }, (state) => reports.push(state))
    expect(sent).toEqual([['a.md', 'b.md'], ['c.md']])
    expect(reports.map(({ answered, sending, loaded }) => [answered, sending, loaded])).toEqual([
      [0, 2, 0],
      [0, 2, 100],
      [2, 1, 0],
      [2, 1, 100],
    ])
    expect(result.rejected.map(({ filename }) => filename)).toEqual(['a.md', 'c.md'])
    expect(result.uploads).toEqual([7])
  })

  it('stop at the first that fails, which keeps what went in and the files not sent', async () => {
    const items = ['a', 'b', 'c', 'd', 'e'].map((name) => item(`${name}.md`))
    const done: UploadResult = { documents: [], rejected: [{ filename: 'a.md', reason: 'No.', limit: null }], upload_id: 3 }
    const refusal = new ApiError(429, 'Too many uploads.')
    const send = vi.fn().mockResolvedValueOnce(done).mockRejectedValueOnce(refusal)
    const stopped = await sendInBatches(items, limits, send, () => {}).catch((error: unknown) => error)
    expect(stopped).toBeInstanceOf(UploadStopped)
    const { error, result, unsent } = stopped as UploadStopped
    expect(error).toBe(refusal)
    expect(result.rejected).toEqual(done.rejected)
    // What went in before is followed all the same.
    expect(result.uploads).toEqual([3])
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

const status = (counts: Partial<UploadStatus> = {}): UploadStatus => ({
  documents: 0,
  ready: 0,
  partly_indexed: 0,
  indexing: 0,
  retrying: 0,
  retry_at: null,
  failed: 0,
  failures: [],
  ...counts,
})

const failure = (id: number, filename: string) => ({ id, filename, reason: 'It has no pages.', quarantined: false })

describe('an upload followed as its documents are indexed', () => {
  it('asks about its uploads a hundred at a time, and adds the answers up', () => {
    const uploads = Array.from({ length: FOLLOWED_A_REQUEST + 1 }, (_, n) => n + 1)
    expect(followedBatches(uploads).map((ids) => ids.length)).toEqual([FOLLOWED_A_REQUEST, 1])
    expect(followedBatches([4, 5])).toEqual([[4, 5]])

    const whole = summed([
      status({ documents: 3, ready: 1, indexing: 1, failed: 1, failures: [failure(2, 'b.pdf')] }),
      status({ documents: 2, retrying: 1, ready: 1, partly_indexed: 1, retry_at: '2026-10-23T10:05:00Z' }),
      status({ documents: 1, retrying: 1, retry_at: '2026-10-23T10:01:00Z' }),
    ])
    expect(whole).toEqual(
      status({
        documents: 6,
        ready: 2,
        partly_indexed: 1,
        indexing: 1,
        retrying: 2,
        retry_at: '2026-10-23T10:01:00Z',
        failed: 1,
        failures: [failure(2, 'b.pdf')],
      }),
    )
  })

  it('is asked about every moment while a document is indexed, after the soonest retry while one waits, and no more once settled', () => {
    const now = Date.parse('2026-10-23T10:00:00Z')
    expect(followAfter(status({ documents: 2, indexing: 1, retrying: 1, retry_at: '2026-10-23T10:05:00Z' }), now)).toBe(REFRESH)
    expect(followAfter(status({ documents: 1, retrying: 1, retry_at: '2026-10-23T10:00:30Z' }), now)).toBe(30_000 + REFRESH)
    // A retry hours away (the day's spending limit) is asked about within the minute all the same.
    expect(followAfter(status({ documents: 1, retrying: 1, retry_at: '2026-10-24T00:00:00Z' }), now)).toBe(WAITING_REFRESH)
    // One due already waits for a worker to take it.
    expect(followAfter(status({ documents: 1, retrying: 1, retry_at: '2026-10-23T09:59:00Z' }), now)).toBe(REFRESH)
    const done = status({ documents: 2, ready: 1, failed: 1 })
    expect(settled(done)).toBe(true)
    expect(followAfter(done, now)).toBe(false)
  })

  it('says how many of its documents are ready, and what became of the others', () => {
    const indexing = standingOf(status({ documents: 40, ready: 12, indexing: 26, failed: 2 }), 'Contracts')
    expect(indexing).toEqual({
      title: 'Indexing 40 files uploaded into Contracts',
      line: '12 of 40 ready; 26 being indexed; 2 could not be indexed.',
      percent: 35,
      settled: false,
      // Two failed, and none listed: the answer was cut short of them.
      more: '2 more could not be indexed: the library marks each.',
    })
    const waiting = standingOf(status({ documents: 3, ready: 2, partly_indexed: 1, retrying: 1, retry_at: '2026-10-23T10:00:00Z' }), 'Invoices')
    expect(waiting.line).toBe('2 of 3 ready, 1 of them partly indexed; 1 waiting for a retry.')
    expect(waiting.settled).toBe(false)

    const done = standingOf(status({ documents: 1, ready: 1 }), 'Invoices')
    expect([done.title, done.line, done.percent, done.settled, done.more]).toEqual([
      '1 file uploaded into Invoices',
      '1 of 1 ready.',
      100,
      true,
      null,
    ])
    const failed = standingOf(status({ documents: 2, ready: 1, failed: 1, failures: [failure(2, 'b.pdf')] }), 'Invoices')
    expect([failed.line, failed.more]).toEqual(['1 of 2 ready; 1 could not be indexed.', null])
    expect(standingOf(status(), 'Invoices').line).toBe('They are all in the trash now.')
  })
})
