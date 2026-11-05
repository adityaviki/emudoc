import { describe, expect, it } from 'vitest'
import type { SystemStatus } from './api.ts'
import { askHeld, describeServices } from './status.ts'

// As a deployment answers its members: no names, and a problem as what cannot be done.
const deployment: SystemStatus = {
  llm: {
    available: true,
    agent_model: null,
    index_model: null,
    agent_fallback_model: null,
    index_fallback_model: null,
    error: null,
  },
  embeddings: { ok: true, model: null, error: null },
  workers: { running: true, error: null },
  currency: 'USD',
  uploads: { files: 100, request_bytes: 500 * 1024 * 1024, file_bytes: 100 * 1024 * 1024, extensions: ['.pdf'] },
}

describe('the header', () => {
  it('says nothing of the AI model or search by meaning, usable or not', () => {
    expect(describeServices(deployment)).toEqual([])
    const off: SystemStatus = {
      ...deployment,
      llm: { ...deployment.llm, available: false, error: 'The AI model is unavailable just now.' },
      embeddings: { ok: false, model: null, error: 'Search matches keywords only just now.' },
    }
    expect(describeServices(off)).toEqual([])
  })
})

describe('the background work', () => {
  const stopped: SystemStatus = {
    ...deployment,
    workers: { running: false, error: "Emudoc's background work is not running just now." },
  }

  it('is a line of the header only while no worker runs, in the server’s words', () => {
    expect(describeServices(stopped)).toEqual([
      { label: 'Background work', ok: false, detail: "Emudoc's background work is not running just now." },
    ])
  })

  it('holds a question with no model to answer it or no worker to run the answer', () => {
    expect(askHeld(undefined)).toBeNull()
    expect(askHeld(deployment)).toBeNull()
    expect(askHeld(stopped)).toBe("Emudoc's background work is not running just now.")
    // The model first: without it there is no answer, worker or not.
    const neither: SystemStatus = { ...stopped, llm: { ...deployment.llm, available: false, error: 'The AI model is unavailable just now.' } }
    expect(askHeld(neither)).toBe('The AI model is unavailable just now.')
  })
})
