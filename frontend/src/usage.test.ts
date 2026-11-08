import { describe, expect, it } from 'vitest'
import type { Usage } from './api.ts'
import { describeAnswerUsage, formatCost } from './usage.ts'

const used = (spent: Partial<Usage>): Usage => ({ model: null, cost_micros: null, ...spent })

describe('describeAnswerUsage', () => {
  it('names the model, then the calls, the tokens and the cost', () => {
    const usage = used({ model: 'google:gemini-2.5-flash-lite', calls: 2, input_tokens: 834, output_tokens: 56, cost_micros: 1200 })
    expect(describeAnswerUsage(usage, 'USD')).toBe(`google:gemini-2.5-flash-lite · 2 calls · 834 tokens in, 56 out · ${formatCost(1200, 'USD')}`)
  })

  it('leaves the cost out while the currency is not known', () => {
    expect(describeAnswerUsage(used({ calls: 1, cost_micros: 1200 }), undefined)).toBe('1 call')
  })
})
