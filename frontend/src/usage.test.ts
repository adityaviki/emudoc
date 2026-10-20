import { describe, expect, it } from 'vitest'
import type { Usage } from './api.ts'
import { describeDocumentUsage, describeUsage } from './usage.ts'

const used = (spent: Partial<Usage>): Usage => ({ model: null, cost_micros: null, ...spent })

describe('describeUsage', () => {
  it('names the pages by what was done with them', () => {
    expect(describeUsage(used({ bytes: 45_000, pages: 2 }), 'USD', 'upload')).toBe('45 kB · 2 pages')
    expect(describeUsage(used({ pages: 1 }), 'USD', 'ocr')).toBe('1 page read')
    expect(describeUsage(used({ pages: 3 }), 'USD', 'render')).toBe('3 pages rendered')
  })

  it('says an upload without pages of its own was counted by its text', () => {
    expect(describeUsage(used({ bytes: 45_000, pages: 12 }), 'USD', 'upload', true)).toBe('45 kB · 12 pages of text')
    expect(describeUsage(used({ pages: 1 }), 'USD', 'upload', true)).toBe('1 page of text')
  })

  it('shows nothing of a count of 0, which marks a document counted before text was', () => {
    expect(describeUsage(used({ bytes: 45_000, pages: 0 }), 'USD', 'upload', true)).toBe('45 kB')
  })
})

describe('describeDocumentUsage', () => {
  const usage = { upload: used({ bytes: 45_000, pages: 12 }), embed: used({ inputs: 8, input_tokens: 400 }) }

  it('words the upload of a document without pages as pages of text', () => {
    expect(describeDocumentUsage(usage, 'USD', false)?.detail).toBe('Upload: 45 kB · 12 pages of text\nEmbeddings: 400 tokens · 8 texts embedded')
    expect(describeDocumentUsage(usage, 'USD')?.detail.split('\n')[0]).toBe('Upload: 45 kB · 12 pages')
  })
})
